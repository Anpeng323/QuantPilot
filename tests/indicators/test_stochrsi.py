"""
TDD: StochRSI 随机相对强弱指标
- 先算 Wilder RSI(period=rsi_period)
- StochRSI = (RSI - min(RSI, n)) / (max(RSI, n) - min(RSI, n)) * 100   (窗口 n=stoch_period)
- %D = StochRSI 的 SMA(smooth_period)
输出列：stochrsi (%K 原始) / stochrsi_d (%D 平滑)
"""
import numpy as np
import pandas as pd
import pytest


def _ref_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    return 100 - 100 / (1 + rs)


def _ref_stochrsi(df: pd.DataFrame, rsi_period: int = 14, stoch_period: int = 14, smooth: int = 3):
    rsi = _ref_rsi(df["close"], rsi_period)
    lo = rsi.rolling(stoch_period).min()
    hi = rsi.rolling(stoch_period).max()
    denom = (hi - lo).replace(0, float("nan"))
    stoch = (rsi - lo) / denom * 100
    d = stoch.rolling(smooth).mean()
    return stoch, d


class TestStochRSI:
    def test_columns_and_length(self, sample_kline):
        from indicators.stochrsi import StochRSI
        res = StochRSI().compute(sample_kline.copy())
        assert "stochrsi" in res.columns and "stochrsi_d" in res.columns
        assert len(res) == len(sample_kline)

    def test_no_mutation(self, sample_kline):
        from indicators.stochrsi import StochRSI
        cols = list(sample_kline.columns)
        StochRSI().compute(sample_kline)
        assert list(sample_kline.columns) == cols

    def test_bounds(self, sample_kline):
        from indicators.stochrsi import StochRSI
        res = StochRSI().compute(sample_kline.copy())
        v = res["stochrsi"].dropna()
        assert (v >= -1e-6).all() and (v <= 100 + 1e-6).all()

    def test_flat_returns_nan(self):
        n = 40
        df = pd.DataFrame({
            "open": np.full(n, 10.0), "high": np.full(n, 10.0),
            "low": np.full(n, 10.0), "close": np.full(n, 10.0),
        })
        from indicators.stochrsi import StochRSI
        res = StochRSI().compute(df)
        # RSI 为常数 -> 分母为 0 -> StochRSI 应为 NaN
        assert res["stochrsi"].isna().all()

    def test_cross_check_reference(self, sample_kline):
        from indicators.stochrsi import StochRSI
        res = StochRSI().compute(sample_kline.copy())
        rk, rd = _ref_stochrsi(sample_kline.copy(), 14, 14, 3)
        pd.testing.assert_series_equal(res["stochrsi"], rk, check_names=False, rtol=1e-6)
        pd.testing.assert_series_equal(res["stochrsi_d"], rd, check_names=False, rtol=1e-6)

    def test_metadata(self):
        from indicators.stochrsi import StochRSI
        ind = StochRSI()
        assert ind.name == "stochrsi"
        assert ind.params["rsi_period"] == 14
        assert ind.params["stoch_period"] == 14
        assert ind.params["smooth"] == 3

    def test_invalid_period(self):
        from indicators.stochrsi import StochRSI
        with pytest.raises(ValueError):
            StochRSI(rsi_period=0)
