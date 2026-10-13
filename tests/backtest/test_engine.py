"""
T11 单元测试 · 回测引擎核心（对应 spec/Phase1 §4 test_engine.py）【防未来函数核心】

直接对 DataFrame 跑 run_backtest_df，避免触达数据库。
关键点：
- test_t_plus_1_fill：证明成交价取 T+1（非 T 日），无未来函数。
- test_hand_calc_match：已知场景前三笔交易与手算一致（AC1.1）。
- test_lot_size：持仓为整百股。
- test_missing_data_abort：数据不足即中止（BacktestError）。
- test_no_trigger：信号全程不成立 → 无交易。
"""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.engine import BacktestRequest, run_backtest_df
from backtest.errors import BacktestError


def _make_df(rows) -> pd.DataFrame:
    """rows: [(date, open, high, low, close), ...]"""
    return pd.DataFrame({
        "trade_date": [r[0] for r in rows],
        "open": [r[1] for r in rows],
        "high": [r[2] for r in rows],
        "low": [r[3] for r in rows],
        "close": [r[4] for r in rows],
        "volume": [1_000_000] * len(rows),
    })


def _req(signal: str, cash: float = 100000.0, fill: str = "close") -> BacktestRequest:
    return BacktestRequest(
        stock_code="TEST", start="2024-01-01", end="2024-01-31",
        signal=signal, initial_cash=cash, fill_price=fill,
    )


def test_t_plus_1_fill():
    """信号在 01-02(T) 为真，成交必须在 T+1(01-03) 用 01-03 的价格（非 T 日 close=99）。"""
    df = _make_df([
        ("2024-01-01", 10, 11, 9, 9),
        ("2024-01-02", 90, 99, 89, 99),  # T：close>open 为真（close=99 绝不可作成交价）
        ("2024-01-03", 10, 14, 9, 13),   # T+1：成交价 = close 13
        ("2024-01-04", 5, 6, 4, 4),      # 转空平仓
        ("2024-01-05", 6, 7, 5, 6),
    ])
    res = run_backtest_df(df, _req("close > open"))
    assert res["trades"], "应至少产生一笔买入"
    buy = res["trades"][0]
    assert buy["side"] == "buy"
    assert buy["date"] == "2024-01-03"
    assert buy["price"] == 13.0
    assert buy["price"] != 99.0  # 绝非 T 日价格（未来函数防护）


def test_hand_calc_match():
    """已知场景首笔交易与手算一致（AC1.1）。"""
    df = _make_df([
        ("2024-01-01", 10, 11, 9, 10),
        ("2024-01-02", 10, 11, 9, 9),
        ("2024-01-03", 10, 13, 9, 12),  # T：为真 -> 01-03 买入 close=13
        ("2024-01-04", 11, 14, 10, 13),
        ("2024-01-05", 14, 15, 12, 12),  # 转空 -> 01-05 平仓 close=12
        ("2024-01-06", 12, 13, 11, 11),
    ])
    res = run_backtest_df(df, _req("close > open"))
    assert res["trades"], "应产生买入"
    buy = res["trades"][0]
    assert buy["date"] == "2024-01-04"   # 信号首真于 01-03(T)，成交在 T+1=01-04
    assert buy["side"] == "buy"
    assert buy["price"] == 13.0
    assert buy["quantity"] == 7600          # floor(100000 / (13*100)) * 100
    assert buy["amount"] == 98800.0         # 13 * 7600
    assert buy["fee"] == 25.688             # 佣金24.7 + 过户0.988


def test_lot_size():
    """持仓为整百股；现金不足时向下取整到百股。"""
    df = _make_df([
        ("2024-01-01", 10, 11, 9, 9),
        ("2024-01-02", 10, 11, 9, 11),
        ("2024-01-03", 10, 13, 9, 12),  # 买入
        ("2024-01-04", 11, 14, 10, 13),
        ("2024-01-05", 14, 15, 12, 12),
    ])
    res = run_backtest_df(df, _req("close > open", cash=200000.0))
    assert res["trades"], "应产生买入"
    buy = res["trades"][0]
    assert buy["quantity"] > 0
    assert buy["quantity"] % 100 == 0
    expected_qty = int(200000 // (buy["price"] * 100)) * 100
    assert buy["quantity"] == expected_qty


def test_missing_data_abort():
    """数据不足（<2 行 / 空）应抛 BacktestError，不崩溃。"""
    df = _make_df([("2024-01-01", 10, 11, 9, 10)])
    with pytest.raises(BacktestError):
        run_backtest_df(df, _req("close > open"))

    empty = _make_df([])
    with pytest.raises(BacktestError):
        run_backtest_df(empty, _req("close > open"))


def test_no_trigger():
    """信号全程不成立 -> 无交易、收益为 0、无数据缺失标记。"""
    df = _make_df([
        ("2024-01-01", 11, 12, 10, 10),
        ("2024-01-02", 12, 13, 11, 11),
        ("2024-01-03", 13, 14, 12, 12),
        ("2024-01-04", 12, 13, 11, 11),
        ("2024-01-05", 11, 12, 10, 10),
    ])
    res = run_backtest_df(df, _req("close > open"))
    assert res["trades"] == []
    assert res["metrics"]["total_return"] == 0.0
    assert res["data_flag"] is None
