"""
财经新闻多来源注册表（聚合层）。

设计目标：
- 每个来源实现统一的 NewsSource.fetch() -> list[dict]，返回规范化的新闻 dict：
  {title, summary, url, source, tag, publish_date, stock_code}
- 新增来源只需在 SOURCE_REGISTRY 注册一个子类，符合开闭原则。
- 单源网络失败不影响其他源（fetch 内部全部 try/except 隔离）。

来源家族（source 字段，用于前端筛选）：
- kr36       : 36氪（官方 newsflash API，含精确发布时间）
- eastmoney  : 东方财富（直连搜索 API；按个股或关键词检索，真实东财内容）
- sina       : 新浪财经（直连滚动新闻 API，主财经要闻）
"""
from __future__ import annotations
import re
from datetime import date, datetime
from typing import Dict, List

import requests

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _http_json(url: str, params: Dict = None, headers: Dict = None, timeout: int = 12):
    """GET 并返回解析后的 JSON（兼容 JSONP：自动剥离 cb(...) 包裹）。"""
    h = dict(_HEADERS)
    if headers:
        h.update(headers)
    r = requests.get(url, params=params, headers=h, timeout=timeout)
    r.raise_for_status()
    txt = r.text.strip()
    if txt[:1] in "({[" or "jQuery" in txt[:12]:
        # JSONP：取最外层括号
        s, e = txt.find("("), txt.rfind(")")
        if s != -1 and e != -1:
            txt = txt[s + 1:e]
    return r, txt


_AD_PAT = re.compile(r"(广告|推广|赞助|商务合作|点击购买|立即开户|开户赠|APP下载)", re.I)


def _norm_title(s: str) -> str:
    s = re.sub(r"\s+", "", s or "")
    return s.strip().lower()


def _headline(text: str, maxlen: int = 28) -> str:
    """从正文/摘要首句生成短标题（备用：无独立标题来源时使用）。

    优先在标点处断句；超长则截断并加省略号，使标题与摘要正文不重复。
    """
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if len(t) <= maxlen:
        return t
    head = t[:maxlen]
    for i in range(len(head) - 1, max(0, len(head) - 10), -1):
        if head[i] in "，。；！？、,.;!?":
            return head[:i]
    return head + "…"


def _to_dt(raw: str) -> str:
    """将 36氪时间字段转为 'YYYY-MM-DD HH:MM:SS'；无法解析则原样返回。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        ts = int(raw)
        if ts > 10 ** 11:  # 毫秒级
            ts = ts / 1000
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
    return raw


def _today() -> str:
    return date.today().isoformat()


class NewsSource:
    """所有新闻源的抽象基类。"""

    name: str = "base"

    def fetch(self, limit: int = 50, stock_code: str = "") -> List[Dict]:
        return []

    @staticmethod
    def _item(title="", summary="", url="", tag="", publish_date="",
              stock_code="", source="", publish_time="") -> Dict:
        return {
            "title": (title or "")[:200],
            "summary": (summary or "")[:2000],
            "url": (url or "").strip(),
            "source": source,
            "tag": (tag or "").strip(),
            "publish_date": publish_date or _today(),
            "publish_time": (publish_time or "").strip(),
            "stock_code": (stock_code or "").strip(),
        }


class Kr36Source(NewsSource):
    """36氪快讯（gateway 网关接口，含精确到秒的发布时间）。

    接口：POST https://gateway.36kr.com/api/mis/nav/newsflash/flow
    返回 data.itemList[]，每条 templateMaterial：
      widgetTitle 标题、widgetContent 摘要(HTML)、publishTime 毫秒时间戳，
      itemId 用于拼详情页 https://36kr.com/newsflashes/{itemId}。
    注：该 gateway 域名不走 36kr.com 的 WAF，requests 可直连。
    单个 source 异常不影响其他源（fetch 内部已隔离）。
    """

    name = "kr36"
    _API = "https://gateway.36kr.com/api/mis/nav/newsflash/flow"
    _PAGE = "https://36kr.com/newsflashes/{}"

    def fetch(self, limit: int = 50, stock_code: str = "") -> List[Dict]:
        import time as _t
        body = {
            "partner_id": "web",
            "param": {"siteId": 1, "platformId": 2, "b_id": "",
                      "pageSize": min(max(limit, 1), 50), "pageEvent": 0,
                      "pageCallback": ""},
            "timestamp": int(_t.time() * 1000),
        }
        headers = {
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            "Origin": "https://36kr.com",
            "Referer": "https://36kr.com/",
            "Content-Type": "application/json",
        }
        try:
            r = requests.post(self._API, json=body, headers=headers, timeout=12)
            r.raise_for_status()
            data = r.json()
        except Exception:
            return []
        items = (data.get("data") or {}).get("itemList") or []
        out: List[Dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            tm = it.get("templateMaterial") or {}
            title = _strip_tags(str(tm.get("widgetTitle", "") or "")).strip()
            summary = _strip_tags(str(tm.get("widgetContent", "") or "")).strip()
            iid = str(tm.get("itemId") or it.get("itemId") or "").strip()
            url = self._PAGE.format(iid) if iid else ""
            if not url or not title:
                continue
            pub = _to_dt(str(tm.get("publishTime") or ""))
            out.append(self._item(
                title=title, summary=summary, url=url,
                source="kr36", tag="36氪",
                publish_date=pub[:10] or _today(), publish_time=pub))
            if len(out) >= limit:
                break
        return out


class EastmoneySource(NewsSource):
    """东方财富新闻：直连搜索 API（绕过 akshare 的日期解析 bug）。

    按个股代码或关键词检索，返回真实东方财富 cmsArticle 新闻。
    """

    name = "eastmoney"
    _API = "https://search-api-web.eastmoney.com/search/jsonp"
    _MAIN_KEYWORD = "A股"  # 主新闻流无个股时，检索广义 A 股相关要闻

    def _search(self, keyword: str, page_size: int = 20) -> List[Dict]:
        inner = {
            "uid": "", "keyword": keyword, "type": ["cmsArticleWebOld"],
            "client": "web", "clientType": "web", "clientVersion": "curr",
            "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                                           "pageIndex": 1, "pageSize": page_size,
                                           "preTag": "<em>", "postTag": "</em>"}},
        }
        import time as _t
        params = {
            "cb": "jQuery_%d_%d" % (int(_t.time() * 1000), int(_t.time() * 1000) % 100000),
            "param": __import__("json").dumps(inner, ensure_ascii=False),
            "env": "prd", "app": "news", "cmd": "search", "_": int(_t.time() * 1000),
        }
        try:
            _, txt = _http_json(self._API, params=params,
                                headers={"Referer": "https://so.eastmoney.com/news/"})
            data = __import__("json").loads(txt)
        except Exception:
            return []
        items = (data.get("result") or {}).get("cmsArticleWebOld") or []
        out: List[Dict] = []
        for it in items:
            title = _strip_tags(str(it.get("title", "") or "")).strip()
            content = _strip_tags(str(it.get("content", "") or "")).strip()
            url = str(it.get("url", "") or "").strip()
            if not url or not title:
                continue
            pub = str(it.get("date", "") or "").strip()  # 形如 '2026-10-14 15:59:00'
            out.append(self._item(
                title=title, summary=content, url=url,
                source="eastmoney", tag="东方财富",
                publish_date=pub[:10] or _today(), publish_time=pub))
            if len(out) >= page_size:
                break
        return out

    def fetch(self, limit: int = 50, stock_code: str = "") -> List[Dict]:
        kw = stock_code or self._MAIN_KEYWORD
        return self._search(kw, page_size=min(limit, 50))


class SinaSource(NewsSource):
    """新浪财经滚动新闻（直连 roll API，主财经要闻）。"""

    name = "sina"
    _ROLL = "https://feed.mix.sina.com.cn/api/roll/get"

    def fetch(self, limit: int = 50, stock_code: str = "") -> List[Dict]:
        params = {"pageid": "153", "lid": "2509", "num": min(limit, 50),
                  "page": "1", "encode": "utf-8", "r": "0"}
        try:
            _, txt = _http_json(self._ROLL, params=params,
                                headers={"Referer": "https://finance.sina.com.cn/"})
            data = __import__("json").loads(txt)
        except Exception:
            return []
        items = (data.get("result") or {}).get("data") or []
        out: List[Dict] = []
        for it in items:
            title = str(it.get("title", "") or "").strip()
            url = str(it.get("url", "") or "").strip()
            if not url or not title:
                continue
            summary = str(it.get("intro") or it.get("summary") or it.get("content") or "").strip()
            ctime = str(it.get("ctime") or it.get("time") or "")
            # ctime 通常是时间戳 -> 保留完整发布时间（含时分秒）
            pub_date, pub_time = _today(), ""
            if ctime.isdigit():
                try:
                    dt = datetime.fromtimestamp(int(ctime))
                    pub_date = dt.date().isoformat()
                    pub_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
            out.append(self._item(
                title=title, summary=summary[:2000], url=url,
                source="sina", tag="新浪财经",
                publish_date=pub_date, publish_time=pub_time))
            if len(out) >= limit:
                break
        return out


# ============== 注册表 ==============
SOURCE_REGISTRY: Dict[str, NewsSource] = {
    "kr36": Kr36Source(),
    "eastmoney": EastmoneySource(),
    "sina": SinaSource(),
}

# 前端筛选下拉用的展示名
SOURCE_LABELS = {
    "kr36": "36氪", "eastmoney": "东方财富", "sina": "新浪财经",
}


def fetch_all(sources: List[str] = None, limit: int = 100,
              stock_code: str = "") -> (List[Dict], Dict):
    """并行（顺序）抓取多个源，返回 (合并条目, 各源统计)。

    统计格式: {source_name: {"fetched": int, "error": str|None}}
    单源异常被隔离，不影响其他源。
    """
    names = sources or list(SOURCE_REGISTRY.keys())
    items: List[Dict] = []
    stats: Dict = {}
    for nm in names:
        src = SOURCE_REGISTRY.get(nm)
        if not src:
            stats[nm] = {"fetched": 0, "error": "未知来源"}
            continue
        try:
            got = src.fetch(limit=limit, stock_code=stock_code) or []
            stats[nm] = {"fetched": len(got), "error": None}
            items.extend(got)
        except Exception as e:  # 兜底
            stats[nm] = {"fetched": 0, "error": str(e)[:200]}
    return items, stats


if __name__ == "__main__":
    items, stats = fetch_all(limit=10)
    print("stats:", stats)
    print("total items:", len(items))
