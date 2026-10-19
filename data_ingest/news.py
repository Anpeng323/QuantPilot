"""
新闻/资讯聚合与入库（多来源）。

- 抓取层在 news_sources.py（36氪/东方财富/新浪 注册表）
- 本模块负责：去噪去重、入库 doc 表(doc_type='news')、列表查询、手动录入
- 兼容旧接口：fetch_main / fetch_stock / list_news / add_news

用法：
  python3 -m data_ingest.news                 # 聚合抓取全市场主财经新闻（默认 100 条）
  python3 -m data_ingest.news --limit 50
  python3 -m data_ingest.news --sources eastmoney,rss
  python3 -m data_ingest.news --stock 600519  # 抓单只股票新闻（跨源）
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
import json
import re
from datetime import date
from typing import Dict, List

from etl.db import get_conn
from data_ingest.news_sources import (
    fetch_all, SOURCE_REGISTRY, _norm_title, _AD_PAT,
)


# ============== 去噪 / 去重 ==============
def _dedupe(rows: List[Dict]) -> List[Dict]:
    """按 url md5 + 归一化标题去重，剔除疑似广告。"""
    seen_url, seen_title = set(), set()
    res = []
    for r in rows:
        u = r.get("url", "")
        key = hashlib.md5(u.encode()).hexdigest()[:16] if u else None
        if key and key in seen_url:
            continue
        nt = _norm_title(r.get("title", "") or r.get("summary", ""))
        if nt and nt in seen_title:
            continue
        if _AD_PAT.search(r.get("summary", "") or "") or _AD_PAT.search(r.get("title", "") or ""):
            continue
        if key:
            seen_url.add(key)
        if nt:
            seen_title.add(nt)
        res.append(r)
    return res


# ============== 跨批次 / 跨来源近重复 ==============
def _norm_title_dedup(s: str) -> str:
    """标题归一化（去空白、转小写）用于重复判定。"""
    return re.sub(r"\s+", "", (s or "")).lower()


def _is_near_dup(a: str, b: str) -> bool:
    """同来源内的近重复判定（高置信）。

    排除「股王大涨 / 股王大跌」这类仅一字之差的反义报道：
    - 完全相同或一方包含另一方 → 视为重复
    - 否则仅在「高度相似(>=0.95)且长度接近」时才判重，避免反义误并
    """
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    if abs(len(a) - len(b)) <= max(3, len(a) // 4):
        return difflib.SequenceMatcher(None, a, b).ratio() >= 0.95
    return False


def _pick_redundant(r1, r2) -> str:
    """返回应删除的那条的 doc_id（保留标题更长/更新的一条）。"""
    t1, t2 = len(r1[2] or ""), len(r2[2] or "")
    if t1 != t2:
        return r2[0] if t1 > t2 else r1[0]
    i1 = str(r1[4]) if r1[4] is not None else ""
    i2 = str(r2[4]) if r2[4] is not None else ""
    return r1[0] if i1 >= i2 else r2[0]


def dedupe_existing() -> int:
    """库内去重：同一事件的多条记录只保留信息量最大/最新的一条。

    规则（保守，避免误删）：
    - 同来源：精确相同 / 子串包含 / 高度相似(>=0.95 且长度接近)
    - 跨来源：仅允许「标题互为子串」的高置信同一事件，避免误并近似/反义报道
    返回删除条数。
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT doc_id, source, title, publish_date, ingested_at, metadata "
        "FROM doc WHERE doc_type='news'"
    ).fetchall()
    n = len(rows)
    norm = [_norm_title_dedup(r[2]) for r in rows]
    drop = set()
    for i in range(n):
        if i in drop:
            continue
        for j in range(i + 1, n):
            if j in drop:
                continue
            if rows[i][1] == rows[j][1]:
                dup = _is_near_dup(norm[i], norm[j])
            else:
                dup = bool(norm[i] and norm[j]
                           and (norm[i] in norm[j] or norm[j] in norm[i]))
            if dup:
                drop.add(_pick_redundant(rows[i], rows[j]))
    if drop:
        ph = ",".join(["?"] * len(drop))
        conn.execute(f"DELETE FROM doc WHERE doc_id IN ({ph})", list(drop))
    return len(drop)


# ============== 入库 ==============
def _ts(v):
    """空字符串 -> None（避免写入 TIMESTAMP 报错）。"""
    s = (v or "").strip() if isinstance(v, str) else v
    return s or None


def save_news(rows: List[Dict]) -> int:
    """入库 doc(doc_type='news') 并即时向量化（标题+summary）。返回新增条数。"""
    if not rows:
        return 0
    conn = get_conn()
    pending = []
    n = 0
    for r in rows:
        url = r.get("url", "")
        if not url:
            continue
        doc_id = hashlib.md5(url.encode()).hexdigest()[:16]
        if conn.execute("SELECT 1 FROM doc WHERE doc_id=?", [doc_id]).fetchone():
            continue
        summary = r.get("summary", "") or ""
        conn.execute("""
            INSERT INTO doc
            (doc_id, doc_type, source, stock_code, title, publish_date, publish_time,
             url, chunk_count, metadata)
            VALUES (?, 'news', ?, ?, ?, ?, ?, ?, 0, ?)
        """, [doc_id, r.get("source", "unknown"), r.get("stock_code", "") or "",
              r.get("title", ""), r.get("publish_date", date.today().isoformat()),
              _ts(r.get("publish_time")),
              url, json.dumps({"tag": r.get("tag", ""), "summary": summary},
                              ensure_ascii=False)])
        text = f"{r.get('title', '')}\n{summary}".strip()
        pending.append((doc_id, text, r.get("stock_code", "") or "", r.get("title", "")))
        n += 1
    if pending:
        from knowledge.ingest import embed_batch
        counts = embed_batch(pending, doc_type="news")
        for (doc_id, _, _, _), cnt in zip(pending, counts):
            conn.execute("UPDATE doc SET chunk_count=? WHERE doc_id=?", [cnt, doc_id])
    return n


# ============== 抓取编排 ==============
def run(limit: int = 100, stock_code: str = "", sources: List[str] = None) -> Dict:
    """聚合抓取并入库。返回各源统计 + 新增计数。

    sources: 限定来源（如 ['eastmoney','rss']）；None 表示全部注册源。
    """
    items, stats = fetch_all(sources=sources, limit=limit, stock_code=stock_code)
    before = _count_news()
    items = _dedupe(items)
    added = save_news(items)
    removed = dedupe_existing()
    total_fetched = sum(s.get("fetched", 0) for s in stats.values())
    print(f"资讯聚合：各源 {stats}；去重后 {len(items)} 条，新入库 {added} 条，库内去重 {removed} 条（累计 {_count_news()}）")
    return {
        "per_source": stats,
        "fetched": total_fetched,
        "deduped": len(items),
        "added": added,
        "deduped_db": removed,
        "total_news": _count_news(),
    }


# crawl 为 run 的别名（手动刷新按钮后端调用）
def crawl(limit: int = 100, stock_code: str = "", sources: List[str] = None) -> Dict:
    return run(limit=limit, stock_code=stock_code, sources=sources)


# ============== 兼容旧接口 ==============
def fetch_main(limit: int = 100) -> List[Dict]:
    """全市场主财经新闻（东方财富）。"""
    items, _ = fetch_all(sources=["eastmoney"], limit=limit, stock_code="")
    return [r for r in items if not r.get("stock_code")]


def fetch_stock(stock_code: str, limit: int = 20) -> List[Dict]:
    """单只股票的新闻（跨源）。"""
    items, _ = fetch_all(limit=limit, stock_code=stock_code)
    return items


def _count_news() -> int:
    return get_conn().execute("SELECT COUNT(*) FROM doc WHERE doc_type='news'").fetchone()[0]


def add_news(title: str, url: str, summary: str = "", source: str = "",
             publish_date: str = None, stock_code: str = "", publish_time: str = "") -> Dict:
    """手动录入单条资讯（兼容手动录入入口）。"""
    import uuid as _uuid
    doc_id = hashlib.md5(url.encode()).hexdigest()[:16] if url else _uuid.uuid4().hex[:12]
    conn = get_conn()
    if conn.execute("SELECT 1 FROM doc WHERE doc_id=?", [doc_id]).fetchone():
        return {"doc_id": doc_id, "title": title, "status": "duplicate"}
    conn.execute("""
        INSERT INTO doc
        (doc_id, doc_type, source, stock_code, title, publish_date, publish_time,
         url, chunk_count, metadata)
        VALUES (?, 'news', ?, ?, ?, ?, ?, ?, 0, ?)
    """, [doc_id, source or "manual", stock_code or "", title,
          publish_date or date.today().isoformat(), _ts(publish_time), url,
          json.dumps({"tag": "手动", "summary": summary}, ensure_ascii=False)])
    from knowledge.ingest import embed_doc
    text = f"{title}\n{summary}".strip()
    cnt = embed_doc(doc_id, text, doc_type="news", stock_code=stock_code or "", title=title)
    conn.execute("UPDATE doc SET chunk_count=? WHERE doc_id=?", [cnt, doc_id])
    return {"doc_id": doc_id, "title": title, "status": "added", "chunk_count": cnt}


def list_news(limit: int = 30, stock_code: str = "", source: str = "",
              tag: str = "", q: str = "", page: int = 1) -> List[Dict]:
    """列出已入库资讯（自动抓取 + 手动录入）。

    支持筛选：source（家族前缀匹配，如 'eastmoney' 匹配 'eastmoney'/'eastmoney_cx'）、
    tag、stock_code、关键词 q（标题/摘要）。分页：page>=1，limit 上限 200。
    """
    limit = max(1, min(int(limit), 200))
    page = max(1, int(page))
    conn = get_conn()
    sql = ("SELECT doc_id, title, url, source, publish_date, publish_time, "
           "stock_code, metadata FROM doc WHERE doc_type='news'")
    params: List = []
    if stock_code:
        sql += " AND stock_code = ?"
        params.append(stock_code)
    if source:
        sql += " AND (source = ? OR source LIKE ?)"
        params.append(source)
        params.append(source + "%")
    if tag:
        sql += " AND metadata LIKE ?"
        params.append(f"%{tag}%")
    if q:
        sql += " AND (title LIKE ? OR metadata LIKE ?)"
        params.append(f"%{q}%")
        params.append(f"%{q}%")
    # 时间倒序：优先精确时间，缺失时退回日期（视为当日 0 点）
    sql += (" ORDER BY COALESCE(publish_time, CAST(publish_date AS TIMESTAMP)) DESC, "
            "ingested_at DESC LIMIT ? OFFSET ?")
    params.append(limit)
    params.append((page - 1) * limit)
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        meta = {}
        if r[7]:
            try:
                meta = json.loads(r[7])
            except Exception:
                meta = {}
        out.append({
            "doc_id": r[0], "title": r[1], "url": r[2], "source": r[3],
            "publish_date": str(r[4]) if r[4] else "",
            "publish_time": str(r[5]) if r[5] else "",
            "stock_code": r[6] or "",
            "tag": meta.get("tag", ""),
            "summary": meta.get("summary", ""),
        })
    from etl.industry import enrich_with_board
    enrich_with_board(out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--stock", type=str, default="", help="指定股票代码则抓该股新闻（跨源）")
    ap.add_argument("--sources", type=str, default="", help="限定来源，逗号分隔，如 eastmoney,rss")
    a = ap.parse_args()
    srcs = [s.strip() for s in a.sources.split(",") if s.strip()] or None
    run(limit=a.limit, stock_code=a.stock, sources=srcs)
