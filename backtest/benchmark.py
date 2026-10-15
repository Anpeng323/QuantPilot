"""基准指数加载（Phase 1 / T4）。用于绩效对比（如沪深300）。"""
from __future__ import annotations

import pandas as pd

from etl.db import get_conn
from .data import _norm_date

# 默认基准：沪深300（库内 index_code 形如 sh000300）
DEFAULT_BENCHMARK = "sh000300"

_INDEX_NAMES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sh000300": "沪深300",
    "sh000905": "中证500",
    "sz399006": "创业板指",
    "sh000688": "科创50",
    "sz399005": "中小板指",
    "sh000016": "上证50",
}


def normalize_index_code(code: str) -> list[str]:
    """把指数代码的各种写法归一成候选查询列表（按优先顺序）。

    支持 'sh000300' / 'SH000300' / '000300.SH' / '000300.SS' / '000300'。
    """
    raw = str(code or "").strip()
    if not raw:
        return []
    up = raw.upper().replace(" ", "")
    cands: list[str] = []

    if "." in up:                                  # 000300.SH → sh000300
        num, _, mkt = up.partition(".")
        mkt = "sz" if mkt in ("SZ", "SZE") else "sh"
        if mkt == "sh":
            cands += [f"sh{num}", f"{num}.SH", num]
        else:
            cands += [f"sz{num}", f"{num}.SZ", num]
    elif up[:2] in ("SH", "SZ", "SS"):
        mkt = "sz" if up[:2] == "SZ" else "sh"
        num = up[2:]
        cands += [f"{mkt}{num}", f"{num}.{'SH' if mkt == 'sh' else 'SZ'}", num]
    else:                                          # 裸数字：000/6 开头归上交所，399 归深交所
        num = up
        mkt = "sz" if num.startswith("399") else "sh"
        other = "sz" if mkt == "sh" else "sh"
        cands += [f"{mkt}{num}", f"{other}{num}", f"{num}.{'SH' if mkt == 'sh' else 'SZ'}", num]

    out, seen = [], set()
    for c in cands:
        c = c.lower()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def load_benchmark(index_code: str, start: str, end: str):
    """
    读取指数日线收盘，返回含 trade_date/close 的 DataFrame，按日期升序。
    兼容 'sh000300' / '000300.SH' / '000300' 等写法；查不到时返回空表。
    """
    start_s = _norm_date(start)
    end_s = _norm_date(end)
    cands = normalize_index_code(index_code)
    if not cands:
        return pd.DataFrame(columns=["trade_date", "close"])
    conn = get_conn(read_only=True)
    try:
        for code in cands:
            df = conn.execute(
                """
                SELECT trade_date, close
                FROM index_kline
                WHERE index_code = ?
                  AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date
                """,
                [code, start_s, end_s],
            ).df()
            if df is not None and not df.empty:
                return df
    finally:
        conn.close()
    return pd.DataFrame(columns=["trade_date", "close"])


def list_benchmarks() -> list[dict]:
    """列出库内可用于对标的指数，供前端下拉选择。"""
    conn = get_conn(read_only=True)
    try:
        df = conn.execute(
            """
            SELECT index_code, count(*) AS rows, min(trade_date) AS start_date,
                   max(trade_date) AS end_date
            FROM index_kline
            GROUP BY index_code
            ORDER BY index_code
            """
        ).df()
    finally:
        conn.close()
    out = []
    for _, r in df.iterrows():
        code = str(r["index_code"])
        out.append({
            "code": code,
            "name": _INDEX_NAMES.get(code.lower(), code.upper()),
            "rows": int(r["rows"]),
            "start": str(r["start_date"]),
            "end": str(r["end_date"]),
            "default": code.lower() == DEFAULT_BENCHMARK,
        })
    return out
