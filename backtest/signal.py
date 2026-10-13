"""
信号 DSL 解析与求值（Phase 1 / T1）。

语法：
  atom      ::= <col> <op> <col|number>
  group     ::= atom (AND atom)*
  signal    ::= group (OR group)*
  op        ::= > >= < <= == !=   （= 等价于 ==）
  col       ::= 原始列(open/high/low/close/volume/amount) 或 指标输出列
                ma5, rsi14, macd_hist, boll_up, kdj_k, ar, br, adx, plus_di, stochrsi ...

设计要点：
- 不使用 eval；自研分词 + 布尔 Series 组合，杜绝注入。
- AND 优先级高于 OR（先按 OR 切分，组内按 AND 组合）。
- NaN 比较结果为 False（指标预热期不产生信号，避免未来函数）。
- 指标列按需惰性计算并缓存到 DataFrame，重复引用不重复计算。
"""
from __future__ import annotations
import re
import operator
from typing import Callable
from indicators.registry import get_indicator
from .errors import SignalParseError

_RAW_COLS = {
    "open", "high", "low", "close", "volume", "amount",
    "turnover_ratio", "change_pct", "pre_close", "change",
}

# (regex, 指标名, 参数字典构造, 输出列名构造)
_PATTERNS = [
    (re.compile(r"^ma(\d+)$"), "ma", lambda m: {"period": int(m.group(1))}, lambda m: f"ma{m.group(1)}"),
    (re.compile(r"^rsi(\d+)$"), "rsi", lambda m: {"period": int(m.group(1))}, lambda m: f"rsi{m.group(1)}"),
    (re.compile(r"^macd_(hist|dif|dea)$"), "macd", lambda m: {}, lambda m: m.group(1)),
    (re.compile(r"^boll_(up|mid|down)$"), "boll", lambda m: {}, lambda m: f"boll_{m.group(1)}"),
    (re.compile(r"^kdj_(k|d|j)$"), "kdj", lambda m: {}, lambda m: f"kdj_{m.group(1)}"),
    (re.compile(r"^(ar|br)$"), "arbr", lambda m: {}, lambda m: m.group(1)),
    (re.compile(r"^(adx|plus_di|minus_di)$"), "adx", lambda m: {}, lambda m: m.group(1)),
    (re.compile(r"^stochrsi_d?$"), "stochrsi", lambda m: {},
     lambda m: ("stochrsi_d" if m.group(0) == "stochrsi_d" else "stochrsi")),
]

_OPS: dict[str, Callable] = {
    ">": operator.gt, ">=": operator.ge, "==": operator.eq,
    "<": operator.lt, "<=": operator.le, "!=": operator.ne,
}

_NUM = re.compile(r"^-?\d+(\.\d+)?$")
_ATOM = re.compile(r"^([A-Za-z_][\w]*)\s*(>=|<=|==|!=|=|>|<)\s*(.+)$")


def _split_top(s: str, sep: str) -> list[str]:
    return [p.strip() for p in re.split(rf"\s+{sep}\s+", s, flags=re.IGNORECASE) if p.strip()]


def resolve_column(df, token: str, cache: dict) -> "object":
    """解析指标列名 → 返回该列 Series（惰性计算并缓存）。"""
    if token in cache:
        return cache[token]
    if token in _RAW_COLS:
        if token not in df.columns:
            raise SignalParseError(f"数据缺少原始列 '{token}'（需行情 OHLCV）")
        s = df[token].astype(float)
        cache[token] = s
        return s
    for pat, ind_name, pb, cb in _PATTERNS:
        m = pat.match(token)
        if not m:
            continue
        try:
            ind = get_indicator(ind_name, **pb(m))
        except KeyError as e:
            raise SignalParseError(str(e))
        out = ind.compute(df)
        out_col = cb(m)  # 实际输出列名，如 macd_hist -> 'hist'
        if out_col not in out.columns:
            raise SignalParseError(f"指标 '{ind_name}' 未产出列 '{out_col}'")
        for col in out.columns:
            if col not in df.columns:
                df[col] = out[col]
            cache.setdefault(col, out[col])
        cache[token] = out[out_col]
        return cache[token]
    raise SignalParseError(
        f"未知指标列: '{token}'。可用示例: ma5, rsi14, macd_hist, boll_up, kdj_k, ar, br, adx, plus_di, stochrsi"
    )


class SignalParser:
    def __init__(self, signal: str):
        self.signal = signal
        self.groups = self._parse(signal)

    def _parse(self, s: str) -> list[list[tuple]]:
        if "(" in s or ")" in s:
            raise SignalParseError("暂不支持括号，请使用 AND/OR 连接原子条件")
        or_parts = _split_top(s, "OR")
        groups = []
        for op_part in or_parts:
            atoms = []
            for atom in _split_top(op_part, "AND"):
                m = _ATOM.match(atom)
                if not m:
                    raise SignalParseError(f"无法解析条件: '{atom}'")
                left = m.group(1)
                op = m.group(2)
                if op == "=":
                    op = "=="
                if op not in _OPS:
                    raise SignalParseError(f"不支持的运算符: '{op}'")
                right_raw = m.group(3).strip()
                atoms.append((left, op, right_raw))
            groups.append(atoms)
        if not groups or not groups[0]:
            raise SignalParseError("信号为空或无法解析")
        return groups

    def validate(self) -> tuple[bool, str]:
        """结构校验（语法层，不需数据）。"""
        try:
            self._parse(self.signal)
            return True, ""
        except SignalParseError as e:
            return False, str(e)

    def evaluate(self, df) -> "object":
        """对含指标列的 DataFrame 求值，返回布尔 Series（NaN→False）。"""
        cache: dict = {}
        or_results = []
        for group in self.groups:
            grp = None
            for left, op, right_raw in group:
                ls = resolve_column(df, left, cache)
                if _NUM.fullmatch(right_raw):
                    rs = float(right_raw)
                else:
                    rs = resolve_column(df, right_raw, cache)
                cmp = _OPS[op](ls, rs)
                grp = cmp if grp is None else (grp & cmp)
            or_results.append(grp)
        result = or_results[0]
        for r in or_results[1:]:
            result = result | r
        return result.fillna(False)


_KNOWN_EXAMPLE = "ma5, rsi14, macd_hist, boll_up, kdj_k, ar, br, adx, plus_di, stochrsi, close, volume"


def _classify_token(token: str) -> str:
    """列名分类：raw（原始列）/ indicator（指标输出列）/ unknown。"""
    if token in _RAW_COLS:
        return "raw"
    for pat, _ind_name, _pb, _cb in _PATTERNS:
        if pat.match(token):
            return "indicator"
    return "unknown"


def validate_signal(signal: str) -> tuple[bool, str]:
    """结构校验 + 列名校验（无需数据即可发现未知指标列）。"""
    try:
        parser = SignalParser(signal)
    except SignalParseError as e:
        return False, str(e)
    for group in parser.groups:
        for left, _op, right_raw in group:
            if _classify_token(left) == "unknown":
                return False, f"未知指标列: '{left}'。可用示例: {_KNOWN_EXAMPLE}"
            if not _NUM.fullmatch(right_raw) and _classify_token(right_raw) == "unknown":
                return False, f"未知指标列: '{right_raw}'。可用示例: {_KNOWN_EXAMPLE}"
    return True, ""
