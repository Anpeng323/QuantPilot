"""
TDD: RSI (Relative Strength Index)
- RSI = 100 - 100 / (1 + RS)
- RS = N 日平均上涨幅度 / N 日平均下跌幅度
- 默认 period=14
"""
import pandas as pd
import pytest


class TestRSI:
    def test_rsi_column(self, sample_kline):
        from indicators.rsi import RSI
        result = RSI(period=14).compute(sample_kline.copy())
        assert "rsi14" in result.columns
        assert len(result) == len(sample_kline)

    def test_rsi_range(self, sample_kline):
        """RSI 值必须在 [0, 100] 之间"""
        from indicators.rsi import RSI
        result = RSI(period=14).compute(sample_kline.copy())
        valid = result["rsi14"].dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rsi_custom_period_column_name(self, sample_kline):
        from indicators.rsi import RSI
        result = RSI(period=6).compute(sample_kline.copy())
        assert "rsi6" in result.columns

    def test_rsi_does_not_mutate_input(self, sample_kline):
        from indicators.rsi import RSI
        original_cols = list(sample_kline.columns)
        RSI().compute(sample_kline)
        assert list(sample_kline.columns) == original_cols

    def test_rsi_monotonic_up(self):
        """价格一直上涨时 RSI 应接近 100"""
        from indicators.rsi import RSI
        df = pd.DataFrame({
            "close": list(range(1, 31)),  # 单调递增
        })
        result = RSI(period=14).compute(df)
        assert result["rsi14"].iloc[-1] == pytest.approx(100.0, abs=0.01)

    def test_rsi_monotonic_down(self):
        """价格一直下跌时 RSI 应接近 0"""
        from indicators.rsi import RSI
        df = pd.DataFrame({
            "close": list(range(30, 0, -1)),  # 单调递减
        })
        result = RSI(period=14).compute(df)
        assert result["rsi14"].iloc[-1] == pytest.approx(0.0, abs=0.01)

    def test_rsi_metadata(self):
        from indicators.rsi import RSI
        ind = RSI(period=14)
        assert ind.name == "rsi14"
        assert ind.params["period"] == 14
