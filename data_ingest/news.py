"""
新闻/宏观数据（v1 占位：手动录入）
"""
import uuid
from datetime import datetime
from etl.db import get_conn


def add_news(title: str, url: str, summary: str = "", source: str = "",
             publish_date: str = None) -> dict:
    nid = uuid.uuid4().hex[:12]
    publish_date = publish_date or datetime.now().date().isoformat()
    conn = get_conn()
    conn.execute("""
        INSERT INTO doc (doc_id, doc_type, source, title, url, file_path, publish_date, chunk_count)
        VALUES (?, 'news', ?, ?, ?, ?, ?, 0)
    """, [nid, source, title, url, f"news://{nid}", publish_date])
    return {"doc_id": nid, "title": title, "status": "added"}


def list_news(limit: int = 30) -> list:
    conn = get_conn()
    rows = conn.execute("""
        SELECT doc_id, title, url, source, publish_date
        FROM doc WHERE doc_type='news'
        ORDER BY publish_date DESC LIMIT ?
    """, [limit]).fetchall()
    return [{"doc_id": r[0], "title": r[1], "url": r[2],
             "source": r[3], "publish_date": str(r[4])} for r in rows]
