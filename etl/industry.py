"""个股板块（行业）归属统一取数。

单一权威来源：优先申万一级，其次申万二级，再次国民经济二级，最后国民经济一级
（adata 对部分股票仅提供国民经济行业分类，无申万细分）。
带进程内 TTL 缓存，避免各 Web API 每次请求全表扫描 stock_industry。
"""
from __future__ import annotations

import threading
import time
from typing import Dict

from etl.db import get_conn

_TTL = 3600  # 缓存 1 小时
_cache: Dict[str, object] = {"map": None, "ts": 0.0}
_lock = threading.Lock()


def get_board_map(force: bool = False) -> Dict[str, str]:
    """返回 {stock_code: 板块名}。无行业数据或异常时返回空字典。"""
    now = time.time()
    with _lock:
        if not force and _cache["map"] is not None and now - _cache["ts"] < _TTL:
            return _cache["map"]  # type: ignore[return-value]

    try:
        conn = get_conn(read_only=True)
        try:
            rows = conn.execute(
                "SELECT stock_code, industry_name, industry_type FROM stock_industry"
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        # 异常时回退旧缓存（若有），否则空字典
        with _lock:
            return _cache["map"] or {}

    sw1, sw2, gb2, gb1 = {}, {}, {}, {}
    for code, name, itype in rows:
        if not code or not name:
            continue
        if itype == "申万一级":
            sw1.setdefault(code, name)
        elif itype == "申万二级":
            sw2.setdefault(code, name)
        elif itype == "一级":
            gb1.setdefault(code, name)
        elif itype == "二级":
            gb2.setdefault(code, name)

    out: Dict[str, str] = {}
    for code in set(sw1) | set(sw2) | set(gb1) | set(gb2):
        out[code] = (sw1.get(code) or sw2.get(code)
                     or gb2.get(code) or gb1.get(code))

    with _lock:
        _cache["map"], _cache["ts"] = out, now
    return out


def enrich_with_board(rows, code_key: str = "stock_code") -> None:
    """就地给 list[dict] 每条记录加 `industry` 字段（无则空串）。"""
    board = get_board_map()
    for r in rows:
        r["industry"] = board.get(r.get(code_key), "") or ""


def list_boards() -> list[str]:
    """全部板块名（去重、按名称排序）。"""
    return sorted({v for v in get_board_map().values() if v})


def match_boards(q: str, limit: int | None = None) -> list[str]:
    """按板块名模糊匹配：完全相等优先，其次是板块名包含查询词。"""
    q = (q or "").strip()
    if not q:
        return []
    names = list_boards()
    exact = [n for n in names if n == q]
    partial = [n for n in names if n != q and q in n]
    hit = exact + partial
    return hit[:limit] if limit else hit


def codes_in_boards(board_names) -> list[str]:
    """返回归属给定板块（任一）的股票代码列表。"""
    names = set(board_names or [])
    if not names:
        return []
    return [c for c, ind in get_board_map().items() if ind in names]


def board_counts() -> Dict[str, int]:
    """{板块名: 成分股数量}。"""
    out: Dict[str, int] = {}
    for ind in get_board_map().values():
        if ind:
            out[ind] = out.get(ind, 0) + 1
    return out
