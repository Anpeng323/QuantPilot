"""
T11 单元测试 · 信号 DSL 解析（对应 spec/Phase1 §4 test_signal.py）
覆盖：原始列比较、指标列（ma/macd）解析、AND/OR 组合、非法输入处理。
不使用 eval；校验 resolve_column 对接 indicators.registry 产出列。
"""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.signal import SignalParser, validate_signal, SignalParseError
from indicators.registry import get_indicator


def _df() -> pd.DataFrame:
    """30 行确定性 K 线（close 单调波动，保证可手算）。"""
    closes = [
        10, 9, 8, 7, 6, 5, 6, 7, 8, 9,
        10, 11, 10, 9, 8, 7, 8, 9, 10, 11,
        12, 11, 10, 9, 8, 9, 10, 11, 12, 13,
    ]
    dates = pd.date_range("2023-01-01", periods=len(closes), freq="B")
    opens = [c - 0.5 for c in closes]
    highs = [c + 0.5 for c in closes]
    lows = [c - 1.0 for c in closes]
    return pd.DataFrame({
        "trade_date": dates,
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": [1_000_000] * len(closes),
    })


def test_parse_close_gt_open():
    df = _df()
    sig = SignalParser("close > open").evaluate(df)
    expected = (df["close"] > df["open"]).fillna(False)
    pd.testing.assert_series_equal(sig, expected, check_names=False)


def test_parse_ma_cross():
    df = _df()
    sig = SignalParser("ma5 > ma20").evaluate(df)
    ma5 = get_indicator("ma", period=5).compute(df)["ma5"]
    ma20 = get_indicator("ma", period=20).compute(df)["ma20"]
    expected = (ma5 > ma20).fillna(False)
    pd.testing.assert_series_equal(sig, expected, check_names=False)


def test_parse_macd_hist():
    df = _df()
    sig = SignalParser("macd_hist > 0").evaluate(df)
    hist = get_indicator("macd").compute(df)["hist"]
    expected = (hist > 0).fillna(False)
    pd.testing.assert_series_equal(sig, expected, check_names=False)


def test_combine_and_or():
    """AND 优先级高于 OR：(close>9 AND open<10) OR (open>11)。"""
    df = _df()
    sig = SignalParser("close > 9 AND open < 10 OR open > 11").evaluate(df)
    g1 = (df["close"] > 9) & (df["open"] < 10)
    g2 = df["open"] > 11
    expected = (g1 | g2).fillna(False)
    pd.testing.assert_series_equal(sig, expected, check_names=False)


def test_invalid_indicator_validate():
    """结构校验应识别未知指标列，并给出可用示例提示。"""
    ok, err = validate_signal("xxx > 0")
    assert ok is False
    assert "未知指标列" in err


def test_invalid_indicator_evaluate_raises():
    """evaluate 阶段对未知列抛 SignalParseError（-> API 映射 400）。"""
    df = _df()
    with pytest.raises(SignalParseError):
        SignalParser("xxx > 0").evaluate(df)


def test_invalid_operator():
    ok, err = validate_signal("close ^ 5")
    assert ok is False
    assert "无法解析" in err


def test_valid_signal_passes():
    ok, err = validate_signal("ma5 > ma20 AND macd_hist > 0")
    assert ok is True
    assert err == ""
