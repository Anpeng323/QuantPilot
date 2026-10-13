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
    """返回 {code: (total_mcap, float_mcap, price, pe, pb)}，缺失为 None。
    pe 取「市盈率-动态」，pb 取「市净率」。"""
    df = ak.stock_zh_a_spot_em()
    out = {}
    for r in df.to_dict("records"):
        code = str(r.get("代码", "")).strip()
        if not code:
            continue
        out[code] = (_num(r.get("总市值")), _num(r.get("流通市值")),
                     _num(r.get("最新价")), _num(r.get("市盈率-动态")),
                     _num(r.get("市净率")))
    return out


def _ensure_valuation_cols(conn):
    """为快照表补 pe / pb 列（兼容已存在表）。"""
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='stock_market_cap'").fetchall()}
    if "pe" not in cols:
        conn.execute("ALTER TABLE stock_market_cap ADD COLUMN pe DOUBLE")
    if "pb" not in cols:
        conn.execute("ALTER TABLE stock_market_cap ADD COLUMN pb DOUBLE")


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
    _ensure_valuation_cols(conn)
    conn.execute("DELETE FROM stock_market_cap")
    rows = [
        (c, today, t, f, p, pe, pb)
        for c, (t, f, p, pe, pb) in spot.items()
        if c in valid
    ]
    if rows:
        conn.executemany(
            "INSERT INTO stock_market_cap VALUES (?,?,?,?,?,?,?)", rows
        )

    # 回填 finance_core（仅该股最新报告期那一行）：
    #   total_market_cap / pe / pb 直接写入；roe 缺失时由 PB/PE 推导（ROE=PB/PE，百分数）
    updated = 0
    roe_filled = 0
    for c, (t, _f, _p, pe, pb) in spot.items():
        if c not in valid:
            continue
        if t is None and pe is None and pb is None:
            continue
        conn.execute("""
            UPDATE finance_core
               SET total_market_cap = COALESCE(?, total_market_cap),
                   pe = COALESCE(?, pe),
                   pb = COALESCE(?, pb)
             WHERE stock_code = ?
               AND report_date = (SELECT MAX(report_date) FROM finance_core WHERE stock_code = ?)
        """, [t, pe, pb, c, c])
        updated += 1
        # ROE 缺失 → 由 PB/PE 推导（仅在两者均有效时）
        if pe not in (None, 0) and pb is not None:
            conn.execute("""
                UPDATE finance_core SET roe = ROUND(?, 2)
                 WHERE stock_code = ? AND roe IS NULL
                   AND report_date = (SELECT MAX(report_date) FROM finance_core WHERE stock_code = ?)
            """, [pb / pe * 100, c, c])
            roe_filled += 1

    print(f"市值快照写入 {len(rows)} 只（含 PE/PB）；"
          f"finance_core 回填 {updated} 只市值/PE/PB，补 ROE {roe_filled} 只")
    return {"written": len(rows), "updated_core": updated, "roe_filled": roe_filled}


if __name__ == "__main__":
    fill()
