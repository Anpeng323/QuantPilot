"""
新闻聚合 API 路由测试（FastAPI TestClient + 临时 DuckDB）。
- 列表筛选 / 个股新闻 / 抓取(打桩) / AI 分析 / 缓存读取
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def news_client(tmp_path, monkeypatch):
    from etl import db as etl_db
    from web import db as web_db
    import data_ingest.news as di_news
    from ai.analyzers import news as news_an

    db_path = tmp_path / "test.duckdb"
    etl_db.DB_PATH = db_path
    web_db.DB_PATH = db_path
    etl_db.init_schema()

    # 数据层模块指向临时库
    monkeypatch.setattr(di_news, "get_conn", lambda: etl_db.get_conn())
    monkeypatch.setattr(news_an, "get_conn", lambda: etl_db.get_conn())
    # crawl 打桩，避免真实触网
    monkeypatch.setattr(di_news, "run", lambda **k: {
        "per_source": {"eastmoney": {"fetched": 0, "error": None}},
        "added": 0, "total_news": 0,
    })

    from web.app import create_app
    from fastapi.testclient import TestClient
    return TestClient(create_app())


def _seed_news(news_client, tmp_path):
    """通过数据层在临时库写入一条新闻，返回 doc_id。"""
    from etl import db as etl_db
    import data_ingest.news as di_news
    di_news.add_news(
        title="测试新闻", url="http://news/seed/1", summary="某公司业绩大幅预增",
        source="eastmoney", stock_code="600519", publish_date="2026-10-10",
    )
    conn = etl_db.get_conn()
    return conn.execute("SELECT doc_id FROM doc WHERE url='http://news/seed/1'").fetchone()[0]


class TestNewsRoutes:
    def test_list_news(self, news_client):
        r = news_client.get("/api/v1/ingest/news?limit=10")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_news_source_filter(self, news_client, tmp_path):
        _seed_news(news_client, tmp_path)
        r = news_client.get("/api/v1/ingest/news?source=eastmoney")
        assert r.status_code == 200
        assert all(it["source"] == "eastmoney" for it in r.json())

    def test_stock_news(self, news_client, tmp_path):
        _seed_news(news_client, tmp_path)
        r = news_client.get("/api/v1/stock/600519/news")
        assert r.status_code == 200
        assert all(it["stock_code"] == "600519" for it in r.json())

    def test_crawl_stub(self, news_client):
        r = news_client.post("/api/v1/ingest/news/crawl", json={"limit": 10})
        assert r.status_code == 200
        body = r.json()
        assert "per_source" in body
        assert "added" in body

    def test_analyze_and_cache(self, news_client, tmp_path):
        doc_id = _seed_news(news_client, tmp_path)
        r = news_client.post("/api/v1/news/analyze", json={"doc_id": doc_id})
        assert r.status_code == 200
        assert "sentiment" in r.json()

        r2 = news_client.get(f"/api/v1/news/analysis/{doc_id}")
        assert r2.status_code == 200
        assert r2.json()["sentiment"] == r.json()["sentiment"]

    def test_analysis_missing_404(self, news_client):
        r = news_client.get("/api/v1/news/analysis/nonexistent")
        assert r.status_code == 404

    def test_analyze_bad_request(self, news_client):
        r = news_client.post("/api/v1/news/analyze", json={})
        assert r.status_code == 400
