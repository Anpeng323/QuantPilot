"""Phase 3 · 自然语言策略自动回测平台 — 验收测试（AC3.1–AC3.5）。

运行：pytest tests/test_nl_backtest.py -q
"""
from __future__ import annotations

import os
import pandas as pd

from backtest.security import validate_code_ast
from backtest.sandbox import execute_signal, SandboxError
from backtest.engine import run_backtest_df, BacktestRequest, BacktestError
from backtest.cost import CostConfig
from backtest import nl as nl_mod


STOCK = "000001"          # 平安银行，库内常有数据
START, END = "2023-01-01", "2024-01-01"


def _req(stock=STOCK, start=START, end=END, signal=""):
    return BacktestRequest(
        stock_code=stock, start=start, end=end, signal=signal,
        initial_cash=100000, fill_price="close", cost=CostConfig(), benchmark=None,
    )


def _dummy_df(n=60):
    import numpy as np
    dates = pd.date_range("2023-01-01", periods=n, freq="D")
    rng = np.random.default_rng(1)
    close = np.cumsum(rng.standard_normal(n)) + 100
    return pd.DataFrame(
        {"trade_date": dates, "open": close, "high": close + 1, "low": close - 1,
         "close": close, "volume": rng.integers(1e5, 1e6, n),
         "amount": rng.integers(1e7, 1e8, n)}
    )


# ---------------------------------------------------------------------------
# AC3.1 端到端：NL 策略绩效与等价 DSL 回测一致（防未来函数沿用 Phase 1）
# ---------------------------------------------------------------------------
def test_nl_matches_dsl():
    desc = "5日均线上穿20日均线"
    res = nl_mod.run_nl_backtest(
        desc, stock_code=STOCK, start=START, end=END, days=None, save=False
    )
    assert res.get("ast_passed") and res.get("executed"), res.get("error")
    assert res.get("disclaimer") is True
    assert "def strategy" in (res.get("generated_code") or "")

    # 等价 DSL
    from backtest.data import load_kline
    dsl_df = load_kline(STOCK, START, END)
    dsl = run_backtest_df(dsl_df, _req(signal="ma5 > ma20"))
    assert res["metrics"] == dsl["metrics"], "NL 与 DSL 绩效应完全一致"

    # 交易层面：引擎核心字段必须逐笔一致（NL 会在核心字段之外附加
    # decide_date / indicator_values / rule_* 等可追溯归因字段，属正常专业化增强，
    # 故只比较引擎产出字段，避免断言随增强字段漂移）。
    CORE_KEYS = ("date", "side", "price", "quantity", "amount", "fee", "pnl")
    _core = lambda t: {k: t.get(k) for k in CORE_KEYS}
    assert [_core(t) for t in res["trades"]] == [_core(t) for t in dsl["trades"]], \
        "NL 与 DSL 交易核心字段应完全一致（防未来函数 / 策略逻辑偏移）"


# ---------------------------------------------------------------------------
# AC3.2 生成代码可审阅 / 可复现
# ---------------------------------------------------------------------------
def test_generated_code_reproducible():
    code1, err1 = nl_mod.generate_code("站上60日线且RSI低于40")
    code2, err2 = nl_mod.generate_code("站上60日线且RSI低于40")
    assert code1 and code2
    assert code1 == code2
    assert "def strategy" in code1


# ---------------------------------------------------------------------------
# AC3.3 安全：AST 白名单拒绝危险语法
# ---------------------------------------------------------------------------
def test_ast_rejects_dangerous():
    cases = [
        "import os\nx=1",
        "import subprocess\nx=1",
        "import socket\nx=1",
        "def strategy(df, i, r):\n    return eval('1+1')",
        "def strategy(df, i, r):\n    return open('/etc/passwd')",
        "def strategy(df, i, r):\n    return __import__('os')",
        "x = (1).__class__",
    ]
    for c in cases:
        ok, reason, _ = validate_code_ast(c)
        assert ok is False, f"应当拒绝：{c}\n原因：{reason}"


def test_ast_allows_whitelist():
    ok, reason, _ = validate_code_ast(
        "import pandas as pd\n"
        "def strategy(df, indicators, runtime):\n"
        "    ma5 = indicators.get_indicator('ma', period=5).compute(df)['ma5']\n"
        "    return df['close'] > ma5"
    )
    assert ok, reason


def test_sandbox_blocks_filesystem_write():
    """沙箱即使拿到写文件代码，builtins.open 被禁用，不会真的落盘。"""
    path = "/tmp/nlbt_no_write_should_not_exist.csv"
    if os.path.exists(path):
        os.remove(path)
    code = (
        "import pandas as pd\n"
        "def strategy(df, indicators, runtime):\n"
        "    pd.DataFrame({'a': [1]}).to_csv('" + path + "')\n"
        "    return df['close'] > df['close']\n"
    )
    ok, reason, _ = validate_code_ast(code)
    # 该代码未用禁止语法，AST 应通过（真正拦截写盘的是沙箱运行时）
    assert ok, reason
    try:
        execute_signal(code, _dummy_df())
    except SandboxError:
        pass  # 被沙箱捕获也算符合预期（没写盘）
    assert not os.path.exists(path), "沙箱不应产生文件写入"


# ---------------------------------------------------------------------------
# AC3.4 合规：结果始终带 disclaimer
# ---------------------------------------------------------------------------
def test_disclaimer_always_true():
    # 正常
    res = nl_mod.run_nl_backtest("MACD红柱", stock_code=STOCK,
                                 start=START, end=END, save=False)
    assert res.get("disclaimer") is True
    # 错误场景（空描述）也应带 disclaimer
    res2 = nl_mod.run_nl_backtest("", stock_code=STOCK, start=START, end=END)
    assert res2.get("disclaimer") is True


# ---------------------------------------------------------------------------
# AC3.5 沙箱超时优雅降级（不卡死）
# ---------------------------------------------------------------------------
def test_sandbox_timeout_graceful():
    os.environ["NLBT_ALARM"] = "4"
    try:
        code = (
            "def strategy(df, indicators, runtime):\n"
            "    while True:\n        pass\n    return df['close'] > df['close']"
        )
        err = None
        try:
            execute_signal(code, _dummy_df(), timeout=12)
        except SandboxError as e:
            err = e
        assert err is not None, "超时应抛出 SandboxError"
        assert err.kind == "timeout"
    finally:
        os.environ.pop("NLBT_ALARM", None)
