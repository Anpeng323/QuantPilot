"""
行业数据接入（v1.1）：申万行业分类，adata 免费源，无需 token。
写入 stock_industry（stock_code, sw_code, industry_name, industry_type, source）。
用法：
  python3 -m data_ingest.industry            # 全量
  python3 -m data_ingest.industry --limit 50 # 前 50 只验证
"""
import sys
import argparse
from etl.db import get_conn
import adata


def fetch_industry(stock_code: str) -> list:
    """返回该股票的申万行业记录列表（含一级/二级）"""
    try:
        df = adata.stock.info.get_industry_sw(stock_code)
    except Exception:
        return []
    if df is None or len(df) == 0:
        return []
    return df.to_dict("records")


def save_industry(stock_code: str, rows: list) -> int:
    conn = get_conn()
    # 先清该股票旧数据，再插入（整批刷新，避免重复）
    conn.execute("DELETE FROM stock_industry WHERE stock_code=?", [stock_code])
    n = 0
    for r in rows:
        try:
            conn.execute(
                """INSERT INTO stock_industry
                   (stock_code, sw_code, industry_name, industry_type, source)
                   VALUES (?, ?, ?, ?, ?)""",
                [stock_code, r.get("sw_code"), r.get("industry_name"),
                 r.get("industry_type"), r.get("source")],
            )
            n += 1
        except Exception:
            pass
    return n


def run(limit: int = None, force: bool = False) -> dict:
    conn = get_conn()
    codes = [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_basic ORDER BY stock_code"
    ).fetchall()]
    if limit:
        codes = codes[:limit]
    total_rows = 0
    ok = 0
    skipped = 0
    for i, code in enumerate(codes, 1):
        # 增量：已有数据则跳过（除非 --force）
        if not force and conn.execute(
            "SELECT 1 FROM stock_industry WHERE stock_code=?", [code]
        ).fetchone():
            skipped += 1
            continue
        rows = fetch_industry(code)
        if rows:
            total_rows += save_industry(code, rows)
            ok += 1
        if i % 200 == 0:
            print(f"  ...{i}/{len(codes)} 已处理（跳过{skipped}）, 行业记录 {total_rows}")
    print(f"行业接入完成：新增 {ok} 只，跳过 {skipped} 只，共 {total_rows} 条新记录")
    return {"stocks": len(codes), "added": ok, "skipped": skipped, "rows": total_rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="忽略已有数据，全量重抓")
    a = ap.parse_args()
    run(limit=a.limit, force=a.force)
