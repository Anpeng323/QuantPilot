"""
价格同步：调用 provider 拉最新价（默认 provider = adata 实时价）。
"""
from __future__ import annotations
from typing import Callable, List, Dict
import time


# Provider 协议：(stock_codes: List[str]) -> Dict[str, float]
_provider: Callable[[List[str]], Dict[str, float]] | None = None


def set_provider(fn=None) -> None:
    """注入 / 清除 provider（测试用）"""
    global _provider
    _provider = fn


def _default_provider_adata(stock_codes: List[str]) -> Dict[str, float]:
    """默认 provider：用 adata 拉实时价（限频，每只票 0.1s）"""
    import adata
    result: Dict[str, float] = {}
    for code in stock_codes:
        try:
            df = adata.stock.market.get_market(stock_code=code, k_type=1, start_date="2020-01-01")
            if df is not None and len(df) > 0:
                row = df.iloc[-1]
                result[code] = float(row["close"])
        except Exception:
            pass
        time.sleep(0.1)
    return result


def sync_prices(conn, stock_codes: List[str]) -> int:
    """
    拉最新价，写入 latest_price 表，返回成功更新的股票数量。
    """
    provider = _provider if _provider is not None else _default_provider_adata
    prices = provider(stock_codes)
    if not prices:
        return 0

    # 1. 创建表（如不存在）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS latest_price (
            stock_code  VARCHAR(6) PRIMARY KEY,
            price       DOUBLE,
            synced_at   TIMESTAMP DEFAULT current_timestamp
        )
    """)

    # 2. 逐条 upsert（先 DELETE 再 INSERT，兼容 DuckDB）
    n = 0
    for stock_code, price in prices.items():
        try:
            conn.execute("DELETE FROM latest_price WHERE stock_code=?", [stock_code])
            conn.execute(
                "INSERT INTO latest_price (stock_code, price, synced_at) VALUES (?, ?, current_timestamp)",
                [stock_code, price],
            )
            n += 1
        except Exception:
            pass
    return n
