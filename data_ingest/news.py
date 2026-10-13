"""
新闻/资讯自动抓取与去噪（akshare 东方财富，免费，无需 token）。

- 全市场主财经新闻：stock_news_main_cx()
- 个股新闻（按需）：stock_news_em(symbol=代码)
- 去噪：按 url 去重、剔除无正文/疑似广告、同批次内按归一化标题二次去重
- 入库：doc 表（doc_type='news'），供前端列表与后续情绪/摘要分析消费

用法：
  python3 -m data_ingest.news                 # 抓全市场主财经新闻（默认 100 条）
  python3 -m data_ingest.news --limit 50
  python3 -m data_ingest.news --stock 600519  # 抓单只股票新闻
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from datetime import date

import akshare as ak
from etl.db import get_conn


_AD_PAT = re.compile(r"(广告|推广|赞助|商务合作|点击购买|立即开户|开户赠)", re.I)


def _norm_title(s: str) -> str:
    s = re.sub(r"\s+", "", s or "")
    return s.strip().lower()


def fetch_main(limit: int = 100) -> list:
    """全市场主财经新闻。返回 [{title, summary, url, source, tag, publish_date}]"""
    try:
        df = ak.stock_news_main_cx()
    except Exception as e:
        print(f"  ! 主财经新闻获取失败: {e}")
        return []
    out = []
    for _, r in df.iterrows():
        url = str(r.get("url", "") or "").strip()
        summary = str(r.get("summary", "") or "").strip()
        tag = str(r.get("tag", "") or "").strip()
        if not url or len(summary) < 8:
            continue
        out.append({
            "title": summary[:80],
            "summary": summary,
            "url": url,
            "source": "eastmoney_cx",
            "tag": tag,
            "publish_date": date.today().isoformat(),
            "stock_code": "",
        })
        if len(out) >= limit:
            break
    return out


def fetch_stock(stock_code: str, limit: int = 20) -> list:
    """单只股票的新闻（按需）。"""
    try:
        df = ak.stock_news_em(symbol=stock_code)
    except Exception as e:
        print(f"  ! {stock_code} 新闻获取失败: {e}")
        return []
    if df is None or len(df) == 0:
        return []
    cols = list(df.columns)
    out = []
    for _, r in df.iterrows():
        # 列名在不同 akshare 版本略有差异，尽力提取
        title = str(r.get("新闻标题", r.get("标题", r.get("news_title", ""))) or "").strip()
        summary = str(r.get("新闻内容", r.get("内容", r.get("news_content", ""))) or "").strip()
        url = str(r.get("新闻链接", r.get("链接", r.get("url", ""))) or "").strip()
        if not url and not summary:
            continue
        if len(summary) < 6 and not title:
            continue
        out.append({
            "title": (title or summary[:80]),
            "summary": summary or title,
            "url": url,
            "source": "eastmoney_stock",
            "tag": "个股",
            "publish_date": date.today().isoformat(),
            "stock_code": stock_code,
        })
        if len(out) >= limit:
            break
    return out


def _dedupe(rows: list) -> list:
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
        # 疑似广告剔除
        if _AD_PAT.search(r.get("summary", "") or "") or _AD_PAT.search(r.get("title", "") or ""):
            continue
        if key:
            seen_url.add(key)
        if nt:
            seen_title.add(nt)
        res.append(r)
    return res


def save_news(rows: list) -> int:
    """入库 doc(doc_type='news')，按 url 去重（跨批次）。返回新增条数。"""
    if not rows:
        return 0
    conn = get_conn()
    n = 0
    for r in rows:
        url = r.get("url", "")
        if not url:
            continue
        doc_id = hashlib.md5(url.encode()).hexdigest()[:16]
        if conn.execute("SELECT 1 FROM doc WHERE doc_id=?", [doc_id]).fetchone():
            continue
        conn.execute("""
            INSERT INTO doc
            (doc_id, doc_type, source, stock_code, title, publish_date, url, chunk_count, metadata)
            VALUES (?, 'news', ?, ?, ?, ?, ?, 0, ?)
        """, [doc_id, r.get("source", "eastmoney"), r.get("stock_code", "") or "",
              r.get("title", ""), r.get("publish_date", date.today().isoformat()),
              url, json.dumps({"tag": r.get("tag", ""), "summary": r.get("summary", "")},
                              ensure_ascii=False)])
        n += 1
    return n


def run(limit: int = 100, stock_code: str = "") -> dict:
    if stock_code:
        rows = fetch_stock(stock_code, limit=limit)
    else:
        rows = fetch_main(limit=limit)
    before = _count_news()
    rows = _dedupe(rows)
    added = save_news(rows)
    print(f"资讯接入：抓到 {len(rows)} 条（去噪后），新入库 {added} 条（doc.news 累计 {before + added}）")
    return {"fetched": len(rows), "added": added, "total_news": before + added}


def _count_news() -> int:
    return get_conn().execute("SELECT COUNT(*) FROM doc WHERE doc_type='news'").fetchone()[0]


def add_news(title: str, url: str, summary: str = "", source: str = "",
             publish_date: str = None, stock_code: str = "") -> dict:
    """手动录入单条资讯（兼容手动录入入口）。"""
    import uuid as _uuid
    doc_id = hashlib.md5(url.encode()).hexdigest()[:16] if url else _uuid.uuid4().hex[:12]
    conn = get_conn()
    if conn.execute("SELECT 1 FROM doc WHERE doc_id=?", [doc_id]).fetchone():
        return {"doc_id": doc_id, "title": title, "status": "duplicate"}
    conn.execute("""
        INSERT INTO doc
        (doc_id, doc_type, source, stock_code, title, publish_date, url, chunk_count, metadata)
        VALUES (?, 'news', ?, ?, ?, ?, ?, 0, ?)
    """, [doc_id, source or "manual", stock_code or "", title,
          publish_date or date.today().isoformat(), url,
          json.dumps({"tag": "手动", "summary": summary}, ensure_ascii=False)])
    return {"doc_id": doc_id, "title": title, "status": "added"}


def list_news(limit: int = 30, stock_code: str = "") -> list:
    """列出已入库资讯（自动抓取 + 手动录入），含摘要与标签。"""
    conn = get_conn()
    sql = ("SELECT doc_id, title, url, source, publish_date, stock_code, metadata "
           "FROM doc WHERE doc_type='news'")
    params = []
    if stock_code:
        sql += " AND stock_code = ?"
        params.append(stock_code)
    sql += " ORDER BY publish_date DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        meta = {}
        if r[6]:
            try:
                meta = json.loads(r[6])
            except Exception:
                meta = {}
        out.append({
            "doc_id": r[0], "title": r[1], "url": r[2], "source": r[3],
            "publish_date": str(r[4]) if r[4] else "",
            "stock_code": r[5] or "",
            "tag": meta.get("tag", ""),
            "summary": meta.get("summary", ""),
        })
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--stock", type=str, default="", help="指定股票代码则抓该股新闻")
    a = ap.parse_args()
    run(limit=a.limit, stock_code=a.stock)
