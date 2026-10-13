"""基准指数加载（Phase 1 / T4）。用于绩效对比（如沪深300）。"""
from __future__ import annotations

from etl.db import get_conn
from .data import _norm_date


def load_benchmark(index_code: str, start: str, end: str):
    """
    读取指数日线收盘，返回含 trade_date/close 的 DataFrame，按日期升序。
    index_code 格式需与 etl 写入 index_kline 时一致（如 '000300.SH'）。
    start/end 接受 'YYYYMMDD' 或 'YYYY-MM-DD'。
    """
    start_s = _norm_date(start)
    end_s = _norm_date(end)
    conn = get_conn(read_only=True)
    try:
        df = conn.execute(
            """
            SELECT trade_date, close
            FROM index_kline
            WHERE index_code = ?
              AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date
            """,
            [index_code, start_s, end_s],
        ).df()
    finally:
        conn.close()
    return df
