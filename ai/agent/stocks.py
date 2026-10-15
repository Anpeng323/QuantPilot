"""股票名称/代码解析：把自然语言里的「茅台」「五粮液」解析成 6 位代码。"""
from __future__ import annotations
import re
from typing import Dict, List, Optional

from etl.db import get_conn

_NAME_MAP = None  # [(code, name), ...] 缓存


def _load_map() -> List[tuple]:
    global _NAME_MAP
    if _NAME_MAP is None:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT stock_code, short_name FROM stock_basic"
            ).fetchall()
        except Exception:
            rows = []
        _NAME_MAP = [(r[0], (r[1] or "")) for r in rows]
    return _NAME_MAP


def _dedup(items) -> List[str]:
    seen, out = set(), []
    for c in items:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def resolve_stock_codes(text: Optional[str]) -> List[str]:
    """从文本中解析出股票代码列表（保序去重）。

    匹配优先级：显式 6 位代码 > 股票全称命中 > 唯一指向的简称别名命中。
    简称别名（取名称前 2 字或第 3~4 字，如「茅台」←「贵州茅台」）只有在
    「该别名只对应唯一一只股票」时才算数，避免「贵州」这类歧义前缀把
    贵州轮胎/贵州百灵 一并误伤。
    """
    if not text:
        return []
    codes: List[str] = list(dict.fromkeys(re.findall(r"\d{6}", text)))

    mp = _load_map()
    full: List[str] = []
    alias_hits: List[tuple] = []  # (code, alias)
    for code, name in mp:
        if not name:
            continue
        if name in text:
            full.append(code)
            continue
        if len(name) >= 3:
            if name[:2] in text:
                alias_hits.append((code, name[:2]))
            elif len(name) >= 4 and name[2:4] in text:
                alias_hits.append((code, name[2:4]))

    # 仅保留唯一指向的别名，规避歧义
    alias_count: Dict[str, int] = {}
    for _, a in alias_hits:
        alias_count[a] = alias_count.get(a, 0) + 1
    alias_codes = [c for c, a in alias_hits if alias_count[a] == 1 and c not in full]

    return _dedup(codes + full + alias_codes)


def name_to_code(name: str) -> Optional[str]:
    mp = _load_map()
    for code, n in mp:
        if n and (n == name or n in name or name in n):
            return code
    return None


def code_to_name(code: str) -> Optional[str]:
    """代码 -> 股票简称（用于生成更易读的追问建议）。"""
    if not code:
        return None
    for c, n in _load_map():
        if c == code:
            return (n or None)
    return None
