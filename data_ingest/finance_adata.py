"""
财务核心数据接入（v1.1）：核心财务指标，adata 免费源，无需 token。
写入 finance_core（stock_code, report_date, announce_date, eps, roe,
  gross_margin, revenue, net_profit, yoy_revenue, yoy_profit,
  pe, pb, ps, total_market_cap）。
说明：get_core_index 提供 eps/roe/revenue/net_profit/yoy，pe/pb/ps/市值暂留空
      （后续可由行情表或估值接口补充）。
用法：
  python3 -m data_ingest.finance_adata            # 全量
  python3 -m data_ingest.finance_adata --limit 50 # 前 50 只验证
"""
import argparse
from etl.db import get_conn
import adata


def fetch_core_index(stock_code: str) -> list:
    try:
        df = adata.stock.finance.get_core_index(stock_code)
    except Exception:
        return []
    if df is None or len(df) == 0:
        return []
    return df.to_dict("records")


def save_core_index(stock_code: str, rows: list) -> int:
    conn = get_conn()
    conn.execute("DELETE FROM finance_core WHERE stock_code=?", [stock_code])
    n = 0
    for r in rows:
        def g(k):
            v = r.get(k)
            if v is None:
                return None
            try:
                import math
                if isinstance(v, float) and math.isnan(v):
                    return None
            except Exception:
                pass
            return v
        try:
            conn.execute(
                """INSERT INTO finance_core
                   (stock_code, report_date, announce_date, eps, roe, gross_margin,
                    revenue, net_profit, yoy_revenue, yoy_profit, pe, pb, ps, total_market_cap)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [stock_code, g("report_date"), g("notice_date"), g("basic_eps"),
                 g("roe_wtd"), g("gross_margin"), g("total_rev"), g("net_profit_attr_sh"),
                 g("total_rev_yoy_gr"), g("net_profit_yoy_gr"),
                 None, None, None, None],
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
            "SELECT 1 FROM finance_core WHERE stock_code=?", [code]
        ).fetchone():
            skipped += 1
            continue
        rows = fetch_core_index(code)
        if rows:
            total_rows += save_core_index(code, rows)
            ok += 1
        if i % 200 == 0:
            print(f"  ...{i}/{len(codes)} 已处理（跳过{skipped}）, 财报记录 {total_rows}")
    print(f"财务接入完成：新增 {ok} 只，跳过 {skipped} 只，共 {total_rows} 条新记录")
    return {"stocks": len(codes), "added": ok, "skipped": skipped, "rows": total_rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="忽略已有数据，全量重抓")
    a = ap.parse_args()
    run(limit=a.limit, force=a.force)
