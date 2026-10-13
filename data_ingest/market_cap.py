"""
全市场总市值 / 流通市值 / 现价快照接入（akshare 东方财富行情，免费，无需 token）。

写入：
  stock_market_cap(stock_code, trade_date, total_market_cap, float_market_cap, price)
并回填 finance_core.total_market_cap（供 DCF 估算总股本、计算每股内在价值）。

说明：stock_zh_a_spot_em() 单次返回全市场约 5900 只 A 股的实时快照，
      含「总市值 / 流通市值 / 最新价」，覆盖面远优于逐只接口，适合每日刷新。

用法：
  python3 -m data_ingest.market_cap          # 刷新快照 + 回填 finance_core
"""
from __future__ import annotations
from datetime import date
from etl.db import get_conn
import akshare as ak


def _num(x):
    try:
        v = float(x)
        return v if v == v else None  # 排除 NaN
    except (TypeError, ValueError):
        return None


def fetch_spot() -> dict:
    """返回 {code: (total_mcap, float_mcap, price)}，缺失为 None。"""
    df = ak.stock_zh_a_spot_em()
    out = {}
    for r in df.to_dict("records"):
        code = str(r.get("代码", "")).strip()
        if not code:
            continue
        out[code] = (_num(r.get("总市值")), _num(r.get("流通市值")), _num(r.get("最新价")))
    return out


def fill(conn=None) -> dict:
    own = conn is None
    if own:
        conn = get_conn()
    spot = fetch_spot()
    today = date.today().isoformat()

    # 仅保留库内股票，避免冗余
    valid = {r[0] for r in conn.execute("SELECT stock_code FROM stock_basic").fetchall()}

    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_market_cap (
            stock_code        VARCHAR,
            trade_date        DATE,
            total_market_cap  DOUBLE,
            float_market_cap  DOUBLE,
            price             DOUBLE
        )
    """)
    conn.execute("DELETE FROM stock_market_cap")
    rows = [
        (c, today, t, f, p)
        for c, (t, f, p) in spot.items()
        if c in valid
    ]
    if rows:
        conn.executemany(
            "INSERT INTO stock_market_cap VALUES (?,?,?,?,?)", rows
        )

    # 回填 finance_core.total_market_cap
    updated = 0
    for c, (t, _f, _p) in spot.items():
        if c not in valid or t is None:
            continue
        conn.execute(
            "UPDATE finance_core SET total_market_cap=? WHERE stock_code=?",
            [t, c],
        )
        updated += 1

    print(f"市值快照写入 {len(rows)} 只；finance_core 回填 {updated} 只 total_market_cap")
    return {"written": len(rows), "updated_core": updated}


if __name__ == "__main__":
    fill()
