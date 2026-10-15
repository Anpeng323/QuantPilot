"""Phase 3 · 自然语言策略自动回测平台（核心编排）。

流程：NL 输入 → LLM 生成策略代码（无 key 时回退启发式）→ AST 白名单校验
→ 受限沙箱执行（算出布尔信号）→ 回测引擎跑绩效 → 落库 strategy_record。

设计要点：
- 沙箱只负责「算信号」——重活（撮合/成本/绩效/DB）仍在主进程引擎里完成，
  既保证与 Phase 1 防未来函数逻辑一致，也把攻击面压到最小。
- 没有 LLM key 时也能用：内置启发式把常见中文描述翻译成指标信号代码。
"""
from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from backtest.engine import (
    BacktestRequest,
    run_backtest_df,
    BacktestError,
    SignalParseError,
)
from backtest.cost import CostConfig
from backtest.data import load_kline
from backtest.security import validate_code_ast
from backtest.sandbox import execute_signal, SandboxError
from indicators import registry as _ind_registry
from etl.db import get_conn


# ---------------------------------------------------------------------------
# 指标目录（给 LLM prompt 与前端 UI 用）
# ---------------------------------------------------------------------------
def _dummy_df(n: int = 60) -> pd.DataFrame:
    dates = pd.date_range("2023-01-01", periods=n, freq="D")
    rng = np.random.default_rng(0)
    close = np.cumsum(rng.standard_normal(n)) + 100
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": close + rng.standard_normal(n),
            "high": close + np.abs(rng.standard_normal(n)),
            "low": close - np.abs(rng.standard_normal(n)),
            "close": close,
            "volume": rng.integers(1_000_00, 9_000_00, n),
            "amount": rng.integers(1_000_000, 9_000_000, n),
        }
    )


def describe_indicators() -> List[Dict[str, Any]]:
    """枚举可用指标：名称、输出列、参数规格。"""
    dummy = _dummy_df(60)
    out: List[Dict[str, Any]] = []
    for name in _ind_registry.list_indicators():
        cls = _ind_registry.REGISTRY[name]
        try:
            computed = cls().compute(dummy.copy())
            outputs = [c for c in computed.columns if c not in dummy.columns]
        except Exception:
            outputs = []
        params: List[Dict[str, Any]] = []
        try:
            for ps in cls.param_specs():
                params.append(
                    {
                        "name": ps.name,
                        "type": ps.type,
                        "default": ps.default,
                        "min": ps.min,
                        "max": ps.max,
                        "description": ps.description,
                    }
                )
        except Exception:
            params = []
        out.append({"name": name, "outputs": outputs, "params": params})
    return out


# ---------------------------------------------------------------------------
# 代码生成：LLM（首选）+ 启发式（回退）
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """你是一名量化策略代码生成器。根据用户的中文自然语言策略描述，\
只输出一段 Python 代码，不要解释、不要 markdown 以外的文字。

代码必须满足以下契约：
- 定义函数：`def strategy(df, indicators, runtime):`
- `df`：pandas.DataFrame，含列 trade_date, open, high, low, close, volume, amount
- `indicators`：指标注册表模块，用法 `indicators.get_indicator('ma', period=5).compute(df)['ma5']`
- `runtime`：工具对象（runtime.compute_indicator / runtime.available）
- 必须返回 pandas.Series（布尔型，与 df 等长同序）：True=持有/买入，False=空仓/卖出
- 仅可使用 pandas / numpy / indicators 与内置数学函数
- 多个条件用 & 组合；均值线用 indicators.get_indicator('ma', period=N).compute(df)['maN']
- 禁止 import os/sys/subprocess/socket、禁止 eval/exec/open、禁止访问 __ 特殊属性
- 回测引擎按「信号状态翻转的次日成交」执行：昨日 False 今日 True → 次日买入；昨日 True 今日 False → 次日卖出。
  因此 signal 表达的是持仓状态而非一次性动作，「上穿买入」应写成状态 ma_short > ma_long（上穿后持续为 True，直到下穿自动卖出）
- 代码内必须用注释写明买卖规则，格式固定为两行：
  `# 买入: <条件成立的含义>` 与 `# 卖出: <条件不再成立的含义>`

可用指标：{catalog}

示例（输入「5日均线上穿20日均线买入，下穿卖出」）：
```python
def strategy(df, indicators, runtime):
    ma5 = indicators.get_indicator('ma', period=5).compute(df)['ma5']
    ma20 = indicators.get_indicator('ma', period=20).compute(df)['ma20']
    # 买入: 5日均线上穿20日均线后（ma5>ma20）次日买入
    # 卖出: 5日均线下穿20日均线后（ma5<=ma20）次日卖出
    signal = ma5 > ma20
    return signal
```
只输出代码本身（含 ```python 围栏）。"""


def _extract_code(text: str) -> Optional[str]:
    if not text:
        return None
    # 去掉 ```python ... ``` 围栏
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    code = m.group(1) if m else text
    # 至少要像函数定义
    if "def strategy" not in code and "return" not in code:
        # 也许围栏外还有正文，尝试直接抓 def strategy 块
        m2 = re.search(r"def strategy\(.*?return .*?", text, re.DOTALL)
        if m2:
            code = m2.group(0)
        else:
            return None
    # 截断到最后一个 return 之后的合理范围（避免尾部多余文字）
    return code.strip()


def _looks_like_strategy(code: Optional[str]) -> bool:
    if not code:
        return False
    return "def strategy" in code and "return" in code


def build_prompt(nl: str, stock_code: Optional[str] = None) -> str:
    catalog = describe_indicators()
    lines = []
    for c in catalog:
        if c["params"]:
            params = "（参数: " + ", ".join(
                "{}={}".format(p["name"], p["default"]) for p in c["params"]
            ) + "）"
        else:
            params = ""
        lines.append("- {}: 输出列 {}{}".format(c["name"], c["outputs"], params))
    cat_text = "\n".join(lines)
    sys_prompt = _SYSTEM_PROMPT.format(catalog=cat_text)
    user = "股票：{}\n策略描述：{}\n\n请只输出策略代码。".format(
        stock_code or "（未指定）", nl
    )
    return sys_prompt + "\n\n" + user


def generate_code(nl: str, stock_code: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """生成策略代码。优先用 LLM（需 key），否则回退启发式。

    返回 (code, llm_error)：
      - code 为 None 时代表未能生成（可能是 LLM 调用失败，也可能是启发式也识别不出）；
      - llm_error 记录 LLM 调用真实报错（用于给前端可懂的提示），无 LLM 参与时为 None。
    """
    import time as _time

    llm_err: Optional[str] = None
    # 1) 尝试 LLM（偶发超时/限流时重试一次）
    try:
        from ai.client import chat, _is_live

        if _is_live():
            prompt = build_prompt(nl, stock_code)
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT.format(
                    catalog="\n".join(
                        f"- {c['name']}: 输出列 {c['outputs']}" for c in describe_indicators()
                    )
                )},
                {"role": "user", "content": f"股票：{stock_code or '（未指定）'}\n策略描述：{nl}\n\n请只输出策略代码。"},
            ]
            for attempt in range(2):
                try:
                    resp = chat(messages, temperature=0.2, max_tokens=1200)
                    code = _extract_code(resp.get("text", ""))
                    if _looks_like_strategy(code):
                        return code, None
                    llm_err = "LLM 返回内容未包含有效策略代码"
                    break
                except Exception as e:  # 限流/超时/网络抖动：重试一次
                    llm_err = f"{type(e).__name__}: {str(e)[:200]}"
                    if attempt < 1:
                        _time.sleep(1.5)
                        continue
    except Exception as e:
        llm_err = f"{type(e).__name__}: {str(e)[:200]}"

    # 2) 回退启发式
    return nl_to_code_heuristic(nl), llm_err


# ---------------------------------------------------------------------------
# 启发式 NL → 代码（无 LLM key 时也能用）
# ---------------------------------------------------------------------------
_LONG_KW = ["站上", "上穿", "上破", "高于", "大于", "突破", "金叉", "多头"]
_EXIT_KW = ["跌破", "下穿", "下破", "低于", "小于", "死叉", "空头"]


def _context_has(text: str, span, keywords) -> bool:
    lo = max(0, span[0] - 10)
    hi = min(len(text), span[1] + 2)
    ctx = text[lo:hi]
    return any(k in ctx for k in keywords)


def nl_to_code_heuristic(nl: str) -> Optional[str]:
    text = nl
    ma_needs: set = set()
    rsi_needs: set = set()
    use_macd = False
    use_vol = False

    long_exprs: List[str] = []
    exit_exprs: List[str] = []

    ma_in_pair = set()

    # --- 均线交叉对：X日均线(上穿|金叉|高于)Y日均线 ---
    for m in re.finditer(
        r"(\d+)\s*日(?:线|均线)[^\d]{0,6}(?:上穿|金叉|高于|大于)[^\d]{0,6}(\d+)\s*日(?:线|均线)",
        text,
    ):
        x, y = int(m.group(1)), int(m.group(2))
        ma_needs.update({x, y})
        ma_in_pair.update({x, y})
        long_exprs.append(f"ma{x} > ma{y}")
    for m in re.finditer(
        r"(\d+)\s*日(?:线|均线)[^\d]{0,6}(?:下穿|死叉|低于|小于)[^\d]{0,6}(\d+)\s*日(?:线|均线)",
        text,
    ):
        x, y = int(m.group(1)), int(m.group(2))
        ma_needs.update({x, y})
        ma_in_pair.update({x, y})
        exit_exprs.append(f"ma{x} < ma{y}")

    # --- 单条均线 vs 价格（跳过已属于交叉对的均线）---
    for m in re.finditer(r"(\d+)\s*日(?:线|均线)", text):
        p = int(m.group(1))
        if p in ma_in_pair:
            continue
        ma_needs.add(p)
        if _context_has(text, (m.start(), m.end()), _LONG_KW):
            long_exprs.append(f"df['close'] > ma{p}")
        elif _context_has(text, (m.start(), m.end()), _EXIT_KW):
            exit_exprs.append(f"df['close'] < ma{p}")

    # --- MA 文本简写 ma5 / MA20 ---
    for m in re.finditer(r"[Mm][Aa](\d+)", text):
        p = int(m.group(1))
        ma_needs.add(p)
        if "金叉" in text or "上穿" in text:
            long_exprs.append(f"ma{p} > ma{p}")  # 容错占位（通常配合另一均线）
        elif "死叉" in text:
            exit_exprs.append(f"ma{p} < ma{p}")

    # --- RSI 阈值 ---
    for m in re.finditer(r"rsi\s*(\d+)?\s*(低于|小于|不大于|<=|<|≤)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE):
        p = int(m.group(1)) if m.group(1) else 14
        v = float(m.group(3))
        rsi_needs.add(p)
        long_exprs.append(f"rsi{p} < {v}")
    for m in re.finditer(r"rsi\s*(\d+)?\s*(高于|大于|>=|>|≥)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE):
        p = int(m.group(1)) if m.group(1) else 14
        v = float(m.group(3))
        rsi_needs.add(p)
        exit_exprs.append(f"rsi{p} > {v}")

    # --- MACD 柱 ---
    if re.search(r"macd.{0,4}(红柱|金叉|大于0|大于 0|hist\s*>|hist>|翻红)", text, re.IGNORECASE):
        use_macd = True
        long_exprs.append("hist > 0")
    if re.search(r"macd.{0,4}(绿柱|死叉|小于0|小于 0|hist\s*<|hist<|翻绿)", text, re.IGNORECASE):
        use_macd = True
        exit_exprs.append("hist < 0")

    # --- 成交量 ---
    if re.search(r"放量|成交量放大|放量上涨|量能放大", text):
        use_vol = True
        long_exprs.append("df['volume'] > vol_ma * 1.5")
    if re.search(r"缩量|成交量萎缩", text):
        use_vol = True
        exit_exprs.append("df['volume'] < vol_ma * 0.7")

    # --- 裸「金叉/死叉」未指明指标：默认按 MACD 金叉/死叉理解（最常见的含糊说法）---
    if not (ma_needs or rsi_needs or use_macd or use_vol) and re.search(r"[金死]叉", text):
        use_macd = True
        if re.search(r"金叉", text):
            long_exprs.append("(dif > dea) & (dif.shift(1) <= dea.shift(1))")
        if re.search(r"死叉", text):
            exit_exprs.append("(dif < dea) & (dif.shift(1) >= dea.shift(1))")

    # --- 突破 N 日新高/新低 ---
    for m in re.finditer(r"突破\s*(\d+)\s*日?\s*(新高|最高)", text):
        n = int(m.group(1))
        long_exprs.append(f"df['close'] >= df['close'].rolling({n}).max()")
    for m in re.finditer(r"跌破\s*(\d+)\s*日?\s*(新低|最低)", text):
        n = int(m.group(1))
        exit_exprs.append(f"df['close'] <= df['close'].rolling({n}).min()")

    if not long_exprs and not exit_exprs:
        return None

    # 每个条件单独加括号，避免 & 优先级高于比较运算符导致的语义错误
    _wrap = lambda e: f"({e})"

    # 组装代码
    lines = ["def strategy(df, indicators, runtime):"]
    for p in sorted(ma_needs):
        lines.append(
            f"    ma{p} = indicators.get_indicator('ma', period={p}).compute(df)['ma{p}']"
        )
    for p in sorted(rsi_needs):
        lines.append(
            f"    rsi{p} = indicators.get_indicator('rsi', period={p}).compute(df)['rsi{p}']"
        )
    if use_macd:
        lines.append("    _macd = indicators.get_indicator('macd').compute(df)")
        lines.append("    dif = _macd['dif']")
        lines.append("    dea = _macd['dea']")
        lines.append("    hist = _macd['hist']")
    if use_vol:
        lines.append("    vol_ma = df['volume'].rolling(20).mean()")

    if long_exprs and exit_exprs:
        signal = " & ".join(_wrap(e) for e in long_exprs) + " & ~(" + " | ".join(_wrap(e) for e in exit_exprs) + ")"
    elif long_exprs:
        signal = " & ".join(_wrap(e) for e in long_exprs)
    else:
        signal = "~(" + " | ".join(_wrap(e) for e in exit_exprs) + ")"
    lines.append(f"    signal = {signal}")
    lines.append("    return signal")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 日期区间解析（默认回看 2 年）
# ---------------------------------------------------------------------------
def resolve_default_range(days: Optional[int]):
    end = _latest_trade_date()
    if end is None:
        end = pd.Timestamp.today().normalize()
    else:
        end = pd.Timestamp(str(end))
    d = int(days) if days else 730
    start = end - timedelta(days=d)
    return end.strftime("%Y-%m-%d"), start.strftime("%Y-%m-%d")


def _latest_trade_date():
    try:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT MAX(trade_date) FROM daily_kline WHERE k_type=1"
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def _err(generated_code, ast_passed, error, **extra) -> Dict[str, Any]:
    out = {
        "generated_code": generated_code,
        "ast_passed": ast_passed,
        "executed": False,
        "error": error,
        "disclaimer": True,
    }
    out.update(extra)
    return out


def run_nl_backtest(
    nl: str,
    stock_code: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    days: Optional[int] = None,
    initial_cash: float = 100000.0,
    fill_price: str = "close",
    cost: Optional[CostConfig] = None,
    benchmark: Optional[str] = None,
    save: bool = False,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """端到端运行一次 NL 回测，始终返回 dict（含错误场景）。"""
    if not (start and end):
        end, start = resolve_default_range(days)

    # 1) 加载行情
    df = load_kline(stock_code, start, end)
    if df is None or df.empty or len(df) < 2:
        return _err(None, False,
                    f"该股票（{stock_code}）在区间 {start}~{end} 内无行情数据，请检查代码或区间")

    # 2) 生成代码
    code, llm_err = generate_code(nl, stock_code)
    if not code:
        if llm_err:
            return _err(None, False,
                        f"大模型生成失败（{llm_err}）。请稍后重试；若持续失败，请检查 MINIMAX_API_KEY / 网络，"
                        f"或改用更明确的描述（如「5日均线上穿20日均线买入，下穿卖出」「MACD金叉买入」）。")
        return _err(None, False,
                    "无法从描述中识别出可用指标。请换用更明确的描述，例如「5日均线上穿20日均线买入，下穿卖出」"
                    "或「MACD金叉买入、死叉卖出」。当前离线（启发式）模式仅支持均线/RSI/MACD/成交量等明确写法。")

    # 3) AST 白名单校验
    ok, reason, suggestions = validate_code_ast(code)
    if not ok:
        return _err(code, False, reason, suggestions=suggestions)

    # 4) 沙箱执行（算出布尔信号）
    try:
        signal_list, _logs = execute_signal(code, df)
    except SandboxError as e:
        return _err(code, True, str(e), sandbox_kind=e.kind)

    # 5) 回测引擎跑绩效（复用 Phase 1，防未来函数一致）
    req = BacktestRequest(
        stock_code=stock_code, start=start, end=end, signal="",
        initial_cash=initial_cash, fill_price=fill_price,
        cost=cost or CostConfig(), benchmark=benchmark, save=False,
    )
    try:
        result = run_backtest_df(df, req, signal=pd.Series(signal_list, index=df.index))
    except (BacktestError, SignalParseError) as e:
        return _err(code, True, str(e), executed=True)

    # 6) 落库（可选）
    strategy_id = None
    duplicate = False
    if save:
        from backtest import repo

        code_hash = hashlib.sha256(
            f"{code}|{stock_code}|{start}|{end}".encode("utf-8")
        ).hexdigest()
        strategy_id, duplicate = repo.save_strategy_record(
            name=name or (nl[:20]),
            source="nl",
            definition=code,
            code_hash=code_hash,
            metrics=result["metrics"],
        )

    kline = _build_kline(df)
    indicators = _build_indicators(df, code)
    rules = _build_rules(code, nl)

    return {
        "generated_code": code,
        "ast_passed": True,
        "executed": True,
        "metrics": result["metrics"],
        # 每笔交易附带「触发规则 + 判定日指标值」，便于在图上标注操作依据
        "trades": _attach_rules(result["trades"], kline, indicators, rules),
        "equity_curve": result["equity_curve"],
        # 行情序列：供前端画 K 线与成交量（顺序与 equity_curve 一致）
        "kline": kline,
        # 指标序列：MACD 子图 + 规则涉及的指标（供提示框显示判定依据）
        "indicators": indicators,
        # 从生成代码里解析出的信号规则描述
        "rules": rules,
        # 执行语义（与引擎实现一致）：信号状态翻转的 T+1 日按所选价格成交
        "execution": {
            "fill_price": "次日开盘价" if fill_price == "open" else "次日收盘价",
            "buy": "信号由 False 变 True 的次日买入（满仓）",
            "sell": "信号由 True 变 False 的次日清仓卖出",
        },
        # 对标基准（如 sh000300）；benchmark_value 全为 None 说明该区间基准数据缺失
        "benchmark": result.get("benchmark"),
        "data_flag": result["data_flag"],
        "strategy_id": strategy_id,
        "duplicate": duplicate,
        "review_url": f"/strategy/{strategy_id}" if strategy_id else None,
        "disclaimer": True,
    }


def _build_kline(df) -> list:
    """把回测区间内的日线整理成前端 K 线需要的 OHLC（含成交量）序列。"""
    cols = {"trade_date", "open", "high", "low", "close"}
    if df is None or getattr(df, "empty", True) or not cols.issubset(set(df.columns)):
        return []
    d = df.sort_values("trade_date")
    has_vol = "volume" in d.columns
    out = []
    for _, r in d.iterrows():
        try:
            item = {
                "date": str(r["trade_date"]),
                "open": round(float(r["open"]), 3),
                "high": round(float(r["high"]), 3),
                "low": round(float(r["low"]), 3),
                "close": round(float(r["close"]), 3),
            }
        except (TypeError, ValueError):
            continue
        if has_vol:
            try:
                item["volume"] = float(r["volume"])
            except (TypeError, ValueError):
                item["volume"] = 0.0
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 指标序列 / 触发规则：给前端「K 线 + 成交量 + MACD + 权益」多子图与买卖点标注用
# ---------------------------------------------------------------------------
_IND_CALL_RE = re.compile(r"""get_indicator\(\s*['"](\w+)['"]\s*((?:\s*,\s*\w+\s*=\s*[^,)]+)*)\)""")
_IND_KW_RE = re.compile(r"(\w+)\s*=\s*([^,)]+)")


def _used_indicators(code: str) -> List[tuple]:
    """解析生成代码里用到的指标及其参数，如 [("macd", {}), ("ma", {"period": 5})]。"""
    out, seen = [], set()
    for m in _IND_CALL_RE.finditer(code or ""):
        name, raw = m.group(1), m.group(2) or ""
        kw: Dict[str, Any] = {}
        for pm in _IND_KW_RE.finditer(raw):
            k, v = pm.group(1), pm.group(2).strip()
            try:
                kw[k] = int(v)
                continue
            except ValueError:
                pass
            try:
                kw[k] = float(v)
            except ValueError:
                pass
        key = (name, tuple(sorted(kw.items())))
        if key in seen:
            continue
        seen.add(key)
        out.append((name, kw))
    return out


def _build_indicators(df, code: str) -> Dict[str, list]:
    """算出与 K 线等长的指标序列：始终含 MACD（供子图），外加代码里用到的指标。"""
    if df is None or getattr(df, "empty", True):
        return {}
    base = df.sort_values("trade_date").reset_index(drop=True)
    series: Dict[str, list] = {}
    for name, kw in [("macd", {})] + _used_indicators(code):
        try:
            out = _ind_registry.get_indicator(name, **kw).compute(base)
        except Exception:
            continue
        base_cols = set(base.columns)
        for col in out.columns:
            if col in base_cols or col in series:
                continue
            try:
                series[col] = [
                    None if pd.isna(v) else round(float(v), 4) for v in out[col]
                ]
            except (TypeError, ValueError):
                continue
    return series


_NOISE_RE = re.compile(
    r"""\.(?:astype\((?:bool|int|float|['"]bool['"]|['"]int['"])\)|fillna\((?:False|True|0|0\.0)\)|values)"""
)
_CMP_RE = re.compile(r"^(.*?)(>=|<=|==|>|<)(.*?)$")
_SHIFT_RE = re.compile(r"\.shift\(\s*\d+\s*\)")
_COL_LABELS = {"dif": "DIF", "dea": "DEA", "hist": "MACD柱", "rsi": "RSI", "kdj": "KDJ", "boll": "BOLL"}
_DF_COL_LABELS = {"close": "收盘价", "open": "开盘价", "high": "最高价", "low": "最低价", "volume": "成交量"}
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*(.+?)\s*$")


def _split_top(expr: str, op: str) -> List[str]:
    """按顶层运算符切分（跳过括号内）。"""
    parts, depth, cur, i = [], 0, "", 0
    while i < len(expr):
        ch = expr[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and expr.startswith(op, i):
            parts.append(cur)
            cur = ""
            i += len(op)
            continue
        cur += ch
        i += 1
    parts.append(cur)
    return [p for p in (p.strip() for p in parts) if p]


def _clean(expr: str) -> str:
    e = _NOISE_RE.sub("", (expr or "").strip())
    return e.strip()


def _bare(expr: str) -> str:
    """去掉「完整包裹」的括号，如 ((a > b)) → a > b；(a) & (b) 不动。"""
    e = _clean(expr)
    while e.startswith("(") and e.endswith(")"):
        depth, matched = 0, False
        for idx, ch in enumerate(e):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    matched = idx == len(e) - 1
                    break
        if not matched:
            return e
        e = e[1:-1].strip()
    return e


def _describe_signal(code: str) -> Dict[str, str]:
    """从生成代码中提取 signal 表达式，翻译成中文规则描述（失败则返回空）。

    返回 {"desc": 完整规则, "short": 图上标签用的短标签, "expr": 原始表达式}
    """
    try:
        return _describe_signal_inner(code or "")
    except Exception:  # 解析失败不影响回测主流程
        return {"desc": "", "short": "", "expr": ""}


def _describe_signal_inner(code: str) -> Dict[str, str]:
    assigns: Dict[str, str] = {}
    for line in code.splitlines():
        line = line.split("#")[0]
        m = _ASSIGN_RE.match(line)
        if m and m.group(1) != "df":
            assigns[m.group(1)] = m.group(2)
    if "signal" not in assigns:
        return {"desc": "", "short": "", "expr": ""}

    labels: Dict[str, str] = {}

    def label_of(name: str, depth: int = 0) -> str:
        if name in labels:
            return labels[name]
        labels[name] = name.upper()          # 占位，防自引用死循环
        expr = assigns.get(name)
        if expr is None or depth > 6:
            return labels[name]
        lbl = _translate(expr, depth + 1) or name.upper()
        labels[name] = lbl
        return lbl

    def _cross(expr: str):
        """识别 (A > B) & (A.shift(1) <= B.shift(1)) 这类穿越写法。"""
        parts = _split_top(_bare(expr), "&")
        if len(parts) != 2:
            return None
        m1, m2 = _CMP_RE.match(_bare(parts[0])), _CMP_RE.match(_bare(parts[1]))
        if not (m1 and m2):
            return None
        l1, o1, r1 = m1.group(1).strip(), m1.group(2), m1.group(3).strip()
        l2, o2, r2 = m2.group(1).strip(), m2.group(2), m2.group(3).strip()
        if _SHIFT_RE.sub("", l1) != _SHIFT_RE.sub("", l2):
            return None
        if _SHIFT_RE.sub("", r1) != _SHIFT_RE.sub("", r2):
            return None
        if o1 in (">", ">=") and o2 in ("<", "<="):
            return f"{_translate(l1, 3)} 上穿 {_translate(r1, 3)}"
        if o1 in ("<", "<=") and o2 in (">", ">="):
            return f"{_translate(l1, 3)} 下穿 {_translate(r1, 3)}"
        return None

    def _indicator_label(expr: str):
        m = re.search(r"""get_indicator\(\s*['"](\w+)['"]\s*(?:,\s*period\s*=\s*(\d+))?""", expr or "")
        if not m:
            return None
        name, period = m.group(1), m.group(2)
        if name == "ma":
            return f"MA{period or ''}"
        if name == "rsi":
            return f"RSI{period or ''}"
        return name.upper()

    def _translate(expr: str, depth: int = 0) -> str:
        """把一段 pandas 表达式翻译成人话。"""
        e = _bare(expr)
        if not e:
            return ""
        cross = _cross(e)
        if cross:
            return cross
        # 复合条件：拆开分别翻译
        for op, word in (("&", " 且 "), ("|", " 或 ")):
            parts = _split_top(e, op)
            if len(parts) > 1:
                done = [p for p in (_translate(p, depth + 1) for p in parts) if p]
                if done:
                    return word.join(done)
        if e.startswith("~"):
            inner = _translate(e[1:], depth + 1)
            return f"非 {inner}" if inner else ""
        m = _CMP_RE.match(e)
        if m:
            left = _translate_operand(m.group(1), depth)
            right = _translate_operand(m.group(3), depth)
            if left and right:
                return f"{left} {m.group(2)} {right}"
        return _translate_operand(e, depth)

    def _translate_operand(raw: str, depth: int) -> str:
        e = _bare(raw)
        if not e:
            return ""
        dfm = re.fullmatch(r"""df\[['"](\w+)['"]\]""", e) or re.fullmatch(r"df\.(\w+)", e)
        if dfm:
            col = dfm.group(1)
            return _DF_COL_LABELS.get(col, col)
        if re.fullmatch(r"-?\d+(\.\d+)?", e):
            return e
        colm = re.fullmatch(r"""[\w.\[\]'"]+\[['"](\w+)['"]\]""", e)
        if colm:
            col = colm.group(1)
            return _COL_LABELS.get(col.lower(), col.upper())
        if re.fullmatch(r"[A-Za-z_]\w*", e):
            return label_of(e, depth)
        ind = _indicator_label(e)
        if ind:
            return ind
        if depth <= 6:
            inner = _translate(e, depth + 1)
            if inner and inner != e:
                return inner
        return ""

    desc = _translate(assigns["signal"])
    if not desc:
        return {"desc": "", "short": "", "expr": assigns["signal"]}
    short = (desc.replace(" 且 ", "&").replace(" 或 ", "|").replace(" ", ""))
    if len(short) > 18:
        short = short[:17] + "…"
    return {"desc": desc, "short": short, "expr": assigns["signal"]}


def _attach_rules(trades: list, kline: list, indicators: Dict[str, list], rules: Dict[str, str]) -> list:
    """给每笔成交补上：判定日、判定日指标值、以及买卖两侧的规则标签。"""
    if not trades:
        return trades
    dates = [k["date"] for k in kline]
    pos = {d: i for i, d in enumerate(dates)}
    keys = list(indicators.keys())[:6]
    out = []
    for t in trades:
        item = dict(t)
        side = t.get("side")
        i = pos.get(t.get("date"))
        di = i - 1 if (i is not None and i >= 1) else None   # T 日成交由 T-1 日信号决定
        item["decide_date"] = dates[di] if di is not None else None
        item["indicator_values"] = (
            {k: indicators[k][di] for k in keys} if di is not None and di < len(dates) else {}
        )
        item["rule_short"] = rules.get("buy_short" if side == "buy" else "sell_short", "")
        item["rule_text"] = rules.get("buy" if side == "buy" else "sell", "")
        # 量化依据：从生成代码里解析出的信号条件（策略描述型规则没有这项）
        item["rule_basis"] = rules.get("expr_desc", "")
        item["rule_source"] = rules.get("source", "")
        out.append(item)
    return out


# 策略描述 → 买卖规则标签（生成代码是循环写法、无法解析出条件表达式时用）
_NL_SPLIT_RE = re.compile(r"[，,；;。\n]|(?:\s+或\s+)|(?:\s+和\s+)")
_NL_BUY_WORDS = ("买入", "买进", "做多", "开仓", "建仓", "持有", "金叉", "上穿")
_NL_SELL_WORDS = ("卖出", "卖掉", "平仓", "清仓", "止盈", "止损", "死叉", "下穿", "做空")
_NL_TAIL_RE = re.compile(r"(?:信号|条件|时|后|则|就|即|的话)+$")
_NL_TAIL_VERB_RE = re.compile(r"(?:并|且|和|、)?\s*(?:买入|买进|卖出|卖掉|开仓|建仓|平仓|清仓|做多|做空|持有|信号)\s*$")
_NL_LEAD_RE = re.compile(r"^(?:当|如果|若|一旦|在)")
_NL_SKIP_LEAD = ("帮我", "请", "写", "生成", "做一", "来一", "给我", "一个")
_NL_COND_WORDS = (
    "均线", "线", "金叉", "死叉", "上穿", "下穿", "突破", "跌破", "站上", "高于", "低于",
    "大于", "小于", "新高", "新低", "放量", "缩量", "rsi", "macd", "kdj", "boll", "adx",
    "stochrsi", ">", "<", "=", "%",
)


def _clean_nl_clause(clause: str) -> str:
    c = _NL_LEAD_RE.sub("", clause.strip()).strip()
    for _ in range(4):                       # 反复剥掉尾部的「并平仓 / 卖出」等纯动作词
        new = _NL_TAIL_VERB_RE.sub("", c).strip()
        if new == c:
            break
        c = new
    return _NL_TAIL_RE.sub("", c).strip(" ：:，,")


def _short_tag(text: str) -> str:
    s = re.sub(r"\s+", "", (text or ""))
    for word in ("成立", "不再成立", "失效", "触发"):
        s = s.replace(word, "")
    s = s.strip("：: ")
    return s[:18] + "…" if len(s) > 18 else s


def _extract_nl_rules(nl: str) -> Dict[str, str]:
    """把策略描述按买卖拆成两侧规则标签，如「MACD金叉买入，死叉卖出」→ 买:MACD金叉 / 卖:死叉。"""
    buy: List[str] = []
    sell: List[str] = []
    for raw in _NL_SPLIT_RE.split(nl or ""):
        clause = (raw or "").strip()
        if not clause or clause.startswith(_NL_SKIP_LEAD):
            continue
        low = clause.lower()
        has_buy = any(w in clause for w in _NL_BUY_WORDS)
        has_sell = any(w in clause for w in _NL_SELL_WORDS)
        if not (has_buy or has_sell or any(w in low for w in _NL_COND_WORDS)):
            continue                # 纯需求描述（如「帮我写个稳健策略」）不算规则
        label = _clean_nl_clause(clause)
        if not label or len(label) > 30:
            continue
        if has_sell and not has_buy:
            sell.append(label)
        else:                       # 其余（含无买卖关键词的条件句）默认算入场条件
            buy.append(label)

    def _join(xs: List[str]) -> str:
        return "、".join(dict.fromkeys(xs))[:40]

    return {"buy": _join(buy), "sell": _join(sell)}


def _build_rules(code: str, nl: str = "") -> Dict[str, str]:
    """买卖两侧的规则说明：优先用策略描述的可读标签，缺哪侧就用代码条件补。"""
    parsed = _describe_signal(code)
    desc = parsed.get("desc", "")
    nl_rules = _extract_nl_rules(nl)
    buy = nl_rules.get("buy") or (f"{desc} 成立" if desc else "")
    sell = nl_rules.get("sell") or (f"{desc} 失效" if desc else "")
    return {
        "expr_desc": desc,
        "expr_short": parsed.get("short", ""),
        "buy": buy,
        "sell": sell,
        "buy_short": _short_tag(buy),
        "sell_short": _short_tag(sell),
        "source": "nl" if (nl_rules.get("buy") or nl_rules.get("sell")) else ("code" if desc else ""),
    }
