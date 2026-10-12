"""
因子计算：用 DuckDB SQL 一次性算出全市场常用 Alpha 因子。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb
from etl.db import get_conn


def compute_basic_factors():
    """
    因子清单：
    - 动量类：mom_5/10/20/60
    - 波动类：vol_20/60 (年化)
    - 量价类：volume_ratio_20d, amount_ratio_20d
    - 价格位置：(close-low_20)/(high_20-low_20)
    - 均线偏离：bias_ma5/20/60
    """
    sql = """
-- DuckDB 不允许嵌套窗口函数，所以先把日收益算出来作为中间 CTE
CREATE OR REPLACE TABLE factors_daily AS
WITH ret AS (
    SELECT
        stock_code,
        trade_date,
        close, high, low, volume, amount,
        close / NULLIF(LAG(close) OVER (PARTITION BY stock_code ORDER BY trade_date), 0) - 1 AS ret_1d
    FROM daily_kline
    WHERE k_type = 1
      AND trade_date >= CURRENT_DATE - INTERVAL '730 days'
)
SELECT
    stock_code,
    trade_date,
    close,

    -- 动量类
    close / NULLIF(LAG(close, 5)  OVER w, 0) - 1 AS mom_5d,
    close / NULLIF(LAG(close, 10) OVER w, 0) - 1 AS mom_10d,
    close / NULLIF(LAG(close, 20) OVER w, 0) - 1 AS mom_20d,
    close / NULLIF(LAG(close, 60) OVER w, 0) - 1 AS mom_60d,

    -- 波动类（年化）
    STDDEV_SAMP(ret_1d) OVER w20 * SQRT(252) AS vol_20d,
    STDDEV_SAMP(ret_1d) OVER w60 * SQRT(252) AS vol_60d,

    -- 量比 / 额比
    volume / NULLIF(AVG(volume) OVER w20, 0) AS volume_ratio_20d,
    amount / NULLIF(AVG(amount) OVER w20, 0) AS amount_ratio_20d,

    -- 价格位置 (20 日内的位置)
    (close - MIN(low)  OVER w20) /
    NULLIF(MAX(high) OVER w20 - MIN(low)  OVER w20, 0) AS price_position_20d,

    -- 均线偏离
    close / NULLIF(AVG(close) OVER w5,  0) - 1 AS bias_ma5,
    close / NULLIF(AVG(close) OVER w20, 0) - 1 AS bias_ma20,
    close / NULLIF(AVG(close) OVER w60, 0) - 1 AS bias_ma60

FROM ret
WINDOW
    w    AS (PARTITION BY stock_code ORDER BY trade_date),
    w5   AS (PARTITION BY stock_code ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW),
    w20  AS (PARTITION BY stock_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
    w60  AS (PARTITION BY stock_code ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW);

CREATE INDEX IF NOT EXISTS idx_factors_sd ON factors_daily(stock_code, trade_date);
CREATE INDEX IF NOT EXISTS idx_factors_d  ON factors_daily(trade_date);
"""
    print("计算基础因子...")
    conn = get_conn()
    conn.execute(sql)
    n = conn.execute("SELECT COUNT(*), COUNT(DISTINCT stock_code) FROM factors_daily").fetchone()
    print(f"  ✓ factors_daily: {n[0]:,} 行 / {n[1]:,} 只票")


def sample_factor_preview(stock_code: str = "000001"):
    with get_conn(read_only=True) as conn:
        df = conn.execute(f"""
            SELECT trade_date, close,
                   ROUND(mom_20d*100, 2) AS mom20_pct,
                   ROUND(vol_20d*100, 2) AS vol20_pct,
                   ROUND(volume_ratio_20d, 2) AS vol_ratio,
                   ROUND(bias_ma20*100, 2) AS bias20_pct
            FROM factors_daily
            WHERE stock_code = '{stock_code}'
            ORDER BY trade_date DESC
            LIMIT 5
        """).df()
    print(f"\n=== {stock_code} 因子预览 ===")
    print(df.to_string(index=False))


if __name__ == "__main__":
    compute_basic_factors()
    sample_factor_preview("000001")
