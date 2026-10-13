"""行情数据加载（Phase 1 / 支撑）。从 daily_kline 读取单股日线。"""
from __future__ import annotations

from typing import Optional

from etl.db import get_conn


def _norm_date(d) -> str:
    """归一化日期为 DATE 列可比较的 'YYYY-MM-DD'；兼容 'YYYYMMDD' 与已是标准格式的输入。"""
    s = str(d).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _query_kline(conn, stock_code: str, start: str, end: str,
                 adjust_type: int, k_type: int) -> Optional["object"]:
    return conn.execute(
        """
        SELECT trade_date, open, high, low, close, volume, amount
        FROM daily_kline
        WHERE stock_code = ?
          AND k_type = ?
          AND adjust_type = ?
          AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
        """,
        [stock_code, k_type, adjust_type, start, end],
    ).df()


def load_kline(stock_code: str, start: str, end: str,
               adjust_type: int = 1, k_type: int = 1):
    """
    读取单股日线（默认前复权 adjust_type=1）。

    数据入库现实：当前 daily_kline 主要写入的是 adjust_type=0（不复权）。
    因此优先取前复权；若前复权无数据，则回退不复权，保证引擎在真实库下可用。
    start/end 接受 'YYYYMMDD' 或 'YYYY-MM-DD'。
    """
    start_s = _norm_date(start)
    end_s = _norm_date(end)
    conn = get_conn(read_only=True)
    try:
        df = _query_kline(conn, stock_code, start_s, end_s, adjust_type, k_type)
        if (df is None or df.empty) and adjust_type != 0:
            # 前复权缺数据 -> 回退不复权（当前库日K主要存 adjust_type=0）
            df = _query_kline(conn, stock_code, start_s, end_s, 0, k_type)
        return df
    finally:
        conn.close()
