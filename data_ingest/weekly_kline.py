"""
周 K 线聚合（复用 daily_kline 表，k_type=2）。
由日 K（k_type=1）按自然周聚合：开=周首开盘，收=周末收盘，高/低=周极值，量/额=周求和。
增量：仅重算「日 K 有更新」的股票（日 K 最大日期 > 周 K 最大日期）。

用法：
  python3 -m data_ingest.weekly_kline          # 增量
  python3 -m data_ingest.weekly_kline --force  # 全量重算
"""
import argparse
from etl.db import get_conn


def aggregate(conn=None, force: bool = False) -> int:
    conn = conn or get_conn()
    if force:
        conn.execute("DELETE FROM daily_kline WHERE k_type=2")
        codes = [r[0] for r in conn.execute(
            "SELECT stock_code FROM daily_kline WHERE k_type=1 GROUP BY 1"
        ).fetchall()]
    else:
        codes = [r[0] for r in conn.execute("""
            SELECT a.stock_code FROM
              (SELECT stock_code, MAX(trade_date) dmax FROM daily_kline WHERE k_type=1 GROUP BY 1) a
            LEFT JOIN
              (SELECT stock_code, MAX(trade_date) wmax FROM daily_kline WHERE k_type=2 GROUP BY 1) b
              ON a.stock_code = b.stock_code
            WHERE a.dmax > COALESCE(b.wmax, '2000-01-01')
        """).fetchall()]
    if not codes:
        return 0
    ph = ",".join("?" * len(codes))
    conn.execute(
        f"DELETE FROM daily_kline WHERE k_type=2 AND stock_code IN ({ph})", codes
    )
    conn.execute(f"""
        WITH base AS (
            SELECT stock_code,
                   date_trunc('week', trade_date) AS wk,
                   MAX(trade_date)               AS week_end,
                   arg_min(open, trade_date)     AS open,
                   MAX(high)                     AS high,
                   MIN(low)                      AS low,
                   arg_max(close, trade_date)    AS close,
                   SUM(volume)                   AS volume,
                   SUM(amount)                   AS amount,
                   arg_min(adjust_type, trade_date) AS adjust_type
            FROM daily_kline WHERE k_type=1 AND stock_code IN ({ph})
            GROUP BY stock_code, date_trunc('week', trade_date)
        ),
        wp AS (
            SELECT *, LAG(close) OVER (PARTITION BY stock_code ORDER BY wk) AS pre_close
            FROM base
        )
        INSERT INTO daily_kline
            (stock_code, trade_date, open, high, low, close, pre_close, change, change_pct,
             volume, amount, turnover_ratio, adjust_type, updated_at, trade_time, k_type)
        SELECT stock_code, week_end, open, high, low, close, pre_close,
               close - pre_close,
               CASE WHEN pre_close > 0 THEN (close - pre_close) / pre_close * 100 ELSE NULL END,
               volume, amount, NULL, 9, now(), NULL, 2
        FROM wp
    """, codes)
    return len(codes)


def run(force: bool = False) -> dict:
    conn = get_conn()
    n = aggregate(conn, force=force)
    print(f"周 K 聚合完成：重算 {n} 只股票的周线（k_type=2）")
    return {"stocks": n}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="全量重算")
    a = ap.parse_args()
    run(force=a.force)
