#!/usr/bin/env python3
"""
RAG 向量库重建：遍历 DuckDB doc 表，把每条文档的正文重新向量化入 Chroma。

用法：
  python scripts/reindex_rag.py                                # 清空并重建全部
  python scripts/reindex_rag.py --doc-type news                # 仅新闻
  python scripts/reindex_rag.py --doc-type announcement --with-body  # 公告尝试抓正文(慢)
  python scripts/reindex_rag.py --no-reset                    # 不清空（增量补向量）

设计：
  - news            : 向量文本 = 标题 + summary（summary 存于 metadata）
  - announcement    : 向量文本 = 标题 + 公告类型；--with-body 时额外抓取网页正文
  - note            : 复用 notes/sync.py（读 note 表 content_md）
  - report/pdf/manual: 复用原始文件（file_path）重新切片向量化
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl.db import get_conn
from knowledge.vector_store import reset as vs_reset, count as vs_count
from knowledge.ingest import embed_batch, reembed_file, fetch_url_text


def _collect(doc_type_filter=None):
    conn = get_conn(read_only=True)
    sql = ("SELECT d.doc_id, d.doc_type, d.stock_code, d.title, d.url, "
           "d.file_path, d.metadata FROM doc d")
    params = []
    if doc_type_filter:
        ph = ",".join(["?"] * len(doc_type_filter))
        sql += f" WHERE d.doc_type IN ({ph})"
        params = list(doc_type_filter)
    return conn.execute(sql, params).fetchall()


def _meta(m):
    try:
        return json.loads(m) if m else {}
    except Exception:
        return {}


def reindex(doc_types=None, with_body=False, no_reset=False):
    t0 = time.time()
    if not no_reset:
        vs_reset()
        get_conn().execute("UPDATE doc SET chunk_count=0")
        print("[reindex] 已清空向量库并将 chunk_count 置 0")

    rows = _collect(doc_types)
    print(f"[reindex] doc 表待处理 {len(rows)} 条")

    news_items, ann_items, notes, files = [], [], [], []
    for doc_id, doc_type, stock_code, title, url, file_path, meta_json in rows:
        meta = _meta(meta_json)
        stock_code = stock_code or ""
        title = title or ""
        if doc_type == "news":
            summary = meta.get("summary", "") or ""
            text = f"{title}\n{summary}".strip()
            news_items.append((doc_id, text, stock_code, title))
        elif doc_type == "announcement":
            ann_type = meta.get("ann_type", "") or ""
            text = title + (f"\n类型：{ann_type}" if ann_type else "")
            if with_body and url:
                body = fetch_url_text(url)
                if body:
                    text += "\n" + body
            ann_items.append((doc_id, text, stock_code, title))
        elif doc_type == "note":
            notes.append(doc_id)
        elif doc_type in ("report", "pdf", "docx", "md", "manual"):
            files.append((doc_id, file_path, doc_type, stock_code, title))
        # 其它未知类型跳过

    conn = get_conn()
    stats = {"news": 0, "announcement": 0, "note": 0, "report": 0, "skipped": 0}

    if news_items:
        counts = embed_batch(news_items, doc_type="news")
        for (doc_id, _, _, _), cnt in zip(news_items, counts):
            conn.execute("UPDATE doc SET chunk_count=? WHERE doc_id=?", [cnt, doc_id])
        stats["news"] = len(news_items)
        print(f"  news 向量化 {len(news_items)} 篇")

    if ann_items:
        counts = embed_batch(ann_items, doc_type="announcement", source="eastmoney")
        for (doc_id, _, _, _), cnt in zip(ann_items, counts):
            conn.execute("UPDATE doc SET chunk_count=? WHERE doc_id=?", [cnt, doc_id])
        stats["announcement"] = len(ann_items)
        print(f"  announcement 向量化 {len(ann_items)} 篇")

    for doc_id in notes:
        try:
            from notes import sync as note_sync
            r = note_sync.sync_note(doc_id)
            if isinstance(r, dict) and r.get("status") == "synced":
                stats["note"] += 1
            else:
                stats["skipped"] += 1
        except Exception as e:
            print(f"  ! note {doc_id} 失败: {e}")
            stats["skipped"] += 1

    for doc_id, file_path, doc_type, stock_code, title in files:
        if file_path and Path(file_path).exists():
            cnt = reembed_file(doc_id, file_path, doc_type=doc_type,
                               stock_code=stock_code, title=title)
            conn.execute("UPDATE doc SET chunk_count=? WHERE doc_id=?", [cnt, doc_id])
            stats["report"] += 1
        else:
            stats["skipped"] += 1

    print(f"[reindex] 完成：{stats} | 向量库条目数 {vs_count()} | 耗时 {round(time.time()-t0, 1)}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc-type", action="append",
                   help="限定类型，可多次，如 news announcement")
    ap.add_argument("--with-body", action="store_true",
                   help="公告尝试抓取网页正文（网络较慢）")
    ap.add_argument("--no-reset", action="store_true",
                   help="不清空向量库（增量补向量模式）")
    a = ap.parse_args()
    reindex(doc_types=a.doc_type, with_body=a.with_body, no_reset=a.no_reset)
