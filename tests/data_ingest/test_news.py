"""
新闻聚合模块单元测试（不触网、使用临时 DuckDB）。
- 多来源注册表 / 聚合 / 去重
- list_news 筛选与分页
- AI 分析器：JSON 解析、落库、缓存读取、批量个股
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from data_ingest import news as di_news
from data_ingest import news_sources as ns
from ai.analyzers import news as news_an


@pytest.fixture
def db(empty_duckdb_conn, monkeypatch):
    """把数据层模块的 get_conn 指向临时库。"""
    monkeypatch.setattr(di_news, "get_conn", lambda: empty_duckdb_conn)
    monkeypatch.setattr(news_an, "get_conn", lambda: empty_duckdb_conn)
    yield empty_duckdb_conn


# ---------------- 注册表 / 聚合 / 去重 ----------------
class TestSourceRegistry:
    def test_registry_has_three_sources(self):
        assert set(ns.SOURCE_REGISTRY.keys()) >= {"kr36", "eastmoney", "sina"}

    def test_fetch_all_aggregates_stub_sources(self, monkeypatch):
        class FakeSrc(ns.NewsSource):
            name = "fake"

            def fetch(self, limit=50, stock_code=""):
                return [
                    {"title": "A", "url": "http://x/1", "summary": "s", "source": "fake"},
                    {"title": "B", "url": "http://x/2", "summary": "s", "source": "fake"},
                ]

        monkeypatch.setattr(ns, "SOURCE_REGISTRY", {"fake": FakeSrc()})
        items, stats = ns.fetch_all(sources=["fake"], limit=10)
        assert len(items) == 2
        assert stats["fake"]["fetched"] == 2
        assert stats["fake"]["error"] is None

    def test_fetch_all_isolates_source_errors(self, monkeypatch):
        class BadSrc(ns.NewsSource):
            name = "bad"

            def fetch(self, limit=50, stock_code=""):
                raise RuntimeError("network down")

        class OkSrc(ns.NewsSource):
            name = "ok"

            def fetch(self, limit=50, stock_code=""):
                return [{"title": "C", "url": "http://x/3", "summary": "s", "source": "ok"}]

        monkeypatch.setattr(ns, "SOURCE_REGISTRY", {"bad": BadSrc(), "ok": OkSrc()})
        items, stats = ns.fetch_all(sources=["bad", "ok"], limit=10)
        assert len(items) == 1
        assert stats["bad"]["error"] is not None
        assert stats["ok"]["fetched"] == 1


class TestDedupe:
    def test_dedupe_by_url_and_ad(self):
        rows = [
            {"title": "A", "url": "http://x/1", "summary": "hello", "source": "eastmoney"},
            {"title": "A", "url": "http://x/1", "summary": "hello", "source": "sina"},  # 同 url
            {"title": "买入良机", "url": "http://x/2", "summary": "利好", "source": "rss"},
            {"title": "点此开户赠金", "url": "http://x/3", "summary": "广告", "source": "eastmoney"},  # 广告
        ]
        out = di_news._dedupe(rows)
        urls = [r["url"] for r in out]
        assert "http://x/1" in urls
        assert "http://x/3" not in urls  # 广告被剔除
        assert len(out) == 2

    def test_dedupe_existing_cross_source_and_rolling(self, db):
        # 同来源高相似但反义（涨/跌）-> 不应合并
        di_news.add_news(title="A股“股王”大涨", url="http://x/g1", summary="x", source="eastmoney", publish_date="2026-10-10")
        di_news.add_news(title="A股“股王”大跌", url="http://x/g2", summary="x", source="eastmoney", publish_date="2026-10-10")
        # 同来源滚动更新（短标题是长标题子串）-> 应合并
        di_news.add_news(title="A股三大指数集体翻红", url="http://x/r1", summary="x", source="eastmoney", publish_date="2026-10-10")
        di_news.add_news(title="A股三大指数集体翻红，超3200只个股下跌", url="http://x/r2", summary="x", source="eastmoney", publish_date="2026-10-10")
        # 跨来源标题子串（同一事件不同来源）-> 应合并
        di_news.add_news(title="央行宣布降准", url="http://x/c1", summary="x", source="eastmoney", publish_date="2026-10-10")
        di_news.add_news(title="央行宣布降准0.5个百分点", url="http://x/c2", summary="x", source="sina", publish_date="2026-10-10")

        removed = di_news.dedupe_existing()
        assert removed == 2, f"应合并 2 条（滚动更新 + 跨来源子串），实际 {removed}"

        titles = [it["title"] for it in di_news.list_news(limit=50)]
        # 反义文章都保留
        assert "A股“股王”大涨" in titles
        assert "A股“股王”大跌" in titles
        # 短标题（被合并掉）不再出现
        assert "A股三大指数集体翻红" not in titles
        assert "央行宣布降准" not in titles


# ---------------- 入库 / 列表筛选 ----------------
class TestListNews:
    def _seed(self, db, n=5, stock=""):
        for i in range(n):
            di_news.add_news(
                title=f"新闻{i}", url=f"http://t/{i}", summary=f"摘要{i}",
                source="eastmoney" if i % 2 == 0 else "sina",
                stock_code=stock, publish_date="2026-10-10",
            )

    def test_list_basic(self, db):
        self._seed(db, 5)
        items = di_news.list_news(limit=10)
        assert len(items) == 5

    def test_list_source_filter(self, db):
        self._seed(db, 5)
        em = di_news.list_news(source="eastmoney")
        assert all(it["source"] == "eastmoney" for it in em)
        assert len(em) == 3  # i=0,2,4

    def test_list_stock_filter(self, db):
        self._seed(db, 3, stock="600519")
        # 再加一条无个股
        di_news.add_news(title="x", url="http://t/99", source="rss", stock_code="")
        res = di_news.list_news(stock_code="600519")
        assert len(res) == 3
        assert all(it["stock_code"] == "600519" for it in res)

    def test_list_pagination(self, db):
        self._seed(db, 5)
        p1 = di_news.list_news(limit=2, page=1)
        p2 = di_news.list_news(limit=2, page=2)
        assert len(p1) == 2 and len(p2) == 2
        assert {it["doc_id"] for it in p1} != {it["doc_id"] for it in p2}

    def test_list_q_filter(self, db):
        self._seed(db, 5)
        res = di_news.list_news(q="新闻1")
        assert all("新闻1" in it["title"] for it in res)


# ---------------- AI 分析器 ----------------
class TestNewsAnalyzer:
    def test_parse_json_normal(self):
        obj = news_an._parse_json('{"sentiment":"利好","summary":"好","impact":"影响"}')
        assert obj["sentiment"] == "利好"

    def test_parse_json_fallback(self):
        obj = news_an._parse_json("这不是 JSON 文本")
        assert obj["sentiment"] == "中性"

    def test_analyze_and_cache(self, db, monkeypatch):
        # 注入结构化 JSON 回复
        def fake_chat(messages, model=None, **kw):
            return {
                "text": '{"sentiment":"利空","summary":"业绩下滑","impact":"承压"}',
                "model": "mock", "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
            }
        monkeypatch.setattr(news_an.llm, "chat", fake_chat)

        di_news.add_news(title="测试", url="http://a/1", summary="某公司业绩下滑", source="eastmoney", stock_code="600519")
        doc_id = di_news.list_news()[0]["doc_id"]

        res = news_an.run(doc_id, save=True)
        assert res["sentiment"] == "利空"
        assert res["summary"] == "业绩下滑"

        cached = news_an.get_analysis(doc_id)
        assert cached is not None
        assert cached["sentiment"] == "利空"

    def test_get_analysis_missing(self, db):
        assert news_an.get_analysis("nope") is None

    def test_analyze_rich_fields_and_cache(self, db, monkeypatch):
        def fake_chat(messages, model=None, **kw):
            return {
                "text": ('{"sentiment":"利好","confidence":"高","summary":"核心解读",'
                         '"key_points":["要点1","要点2"],"impact":"利好新能源",'
                         '"risk":"需求不及预期","watch":"关注销量数据"}'),
                "model": "mock", "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
            }
        monkeypatch.setattr(news_an.llm, "chat", fake_chat)
        di_news.add_news(title="t", url="http://rich/1", summary="某公司获大单", source="eastmoney")
        doc_id = di_news.list_news()[0]["doc_id"]

        res = news_an.run(doc_id, save=True, force=True)
        assert res["confidence"] == "高"
        assert res["key_points"] == ["要点1", "要点2"]
        assert res["risk"] == "需求不及预期"
        assert res["watch"] == "关注销量数据"
        assert res["cached"] is False

        # 二次调用命中同版本缓存
        res2 = news_an.run(doc_id, save=True)
        assert res2["cached"] is True
        assert res2["key_points"] == ["要点1", "要点2"]

        cached = news_an.get_analysis(doc_id)
        assert cached["version"] == news_an.ANALYSIS_VERSION

    def test_run_for_stock(self, db, monkeypatch):
        def fake_chat(messages, model=None, **kw):
            return {
                "text": '{"sentiment":"利好","summary":"好","impact":"影响有限"}',
                "model": "mock", "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
            }
        monkeypatch.setattr(news_an.llm, "chat", fake_chat)
        for i in range(3):
            di_news.add_news(title=f"s{i}", url=f"http://s/{i}", summary="x", source="eastmoney", stock_code="000001")
        res = news_an.run_for_stock("000001")
        assert res["count"] == 3
        assert res["distribution"]["利好"] == 3
