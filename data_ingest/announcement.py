"""
公告数据接入。
- v1 简化：从 DuckDB 的 doc 表空数据中可以看出还没有公告表
- 直接把外部公告（用户提供的或手动抓取的）入库
"""
from etl.db import get_conn


def list_announcements(stock_code: str = "", limit: int = 50) -> list:
    conn = get_conn()
    sql = "SELECT doc_id, stock_code, title, publish_date, source, url FROM doc WHERE doc_type='announcement'"
    params = []
    if stock_code:
        sql += " AND stock_code = ?"
        params.append(stock_code)
    sql += " ORDER BY publish_date DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [{"doc_id": r[0], "stock_code": r[1], "title": r[2],
             "publish_date": str(r[3]), "source": r[4], "url": r[5]} for r in rows]
