"""
公告数据接入（akshare 东方财富，免费，无需 token）。
按交易日抓取全市场公告，入库 doc 表（doc_type='announcement'）。

用法：
  python3 -m data_ingest.announcement            # 抓取最近 1 个交易日
  python3 -m data_ingest.announcement --days 3  # 最近 3 个交易日
  python3 -m data_ingest.announcement --date 20261012
"""
import argparse
import hashlib
from datetime import date
from etl.db import get_conn
import akshare as ak


def _recent_trade_dates(n: int, end: str = None) -> list:
    conn = get_conn()
    end = end or date.today().isoformat()
    rows = conn.execute(
        "SELECT trade_date FROM trade_calendar WHERE trade_status=1 "
        "AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",
        [end, n],
    ).fetchall()
    dates = []
    for r in reversed(rows):
        d = r[0]
        # DuckDB 的 DATE 列返回 datetime.date，用 strftime 转 YYYYMMDD；兜底处理字符串
        dates.append(d.strftime("%Y%m%d") if hasattr(d, "strftime") else str(d).replace("-", ""))
    return dates


def fetch_date(date_str: str) -> list:
    """抓取某交易日全市场公告。date_str 形如 '20261012'（无连字符）。"""
    try:
        df = ak.stock_notice_report(symbol="全部", date=date_str)
    except Exception as e:
        print(f"  ! {date_str} 公告获取失败: {e}")
        return []
    if df is None or len(df) == 0:
        return []
    out = []
    for _, r in df.iterrows():
        code = str(r.get("代码", "") or "").strip()
        if not code:
            continue
        out.append({
            "stock_code": code,
            "title": str(r.get("公告标题", "")).strip(),
            "ann_type": str(r.get("公告类型", "")).strip(),
            "publish_date": str(r.get("公告日期", "")).strip()[:10],
            "url": str(r.get("网址", "")).strip(),
        })
    return out


def save_announcements(rows: list) -> int:
    if not rows:
        return 0
    conn = get_conn()
    n = 0
    for r in rows:
        url = r.get("url")
        if not url:
            continue
        doc_id = hashlib.md5(url.encode()).hexdigest()[:16]
        if conn.execute("SELECT 1 FROM doc WHERE doc_id=?", [doc_id]).fetchone():
            continue
        conn.execute(
            """INSERT INTO doc (doc_id, doc_type, source, stock_code, title, publish_date, url, chunk_count)
               VALUES (?, 'announcement', 'eastmoney', ?, ?, ?, ?, 0)""",
            [doc_id, r["stock_code"], r["title"], r["publish_date"], url],
        )
        n += 1
    return n


def run(days: int = 1, date_str: str = None) -> dict:
    dates = [date_str] if date_str else _recent_trade_dates(days)
    total = 0
    for d in dates:
        rows = fetch_date(d)
        total += save_announcements(rows)
        print(f"  {d}: 抓到 {len(rows)} 条，新入库 {total - (total - len(rows))} (累计 {total})")
    print(f"公告接入完成：新入库 {total} 条（覆盖 {len(dates)} 个交易日）")
    return {"dates": len(dates), "added": total}


def list_announcements(stock_code: str = "", limit: int = 50) -> list:
    """列出已入库公告（供 API GET 使用）"""
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--date", type=str, default=None, help="指定交易日 YYYYMMDD")
    a = ap.parse_args()
    run(days=a.days, date_str=a.date)
