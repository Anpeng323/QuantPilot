"""
TDD: MACD 指标
- DIF = EMA(close, 12) - EMA(close, 26)
- DEA = EMA(DIF, 9)
- HIST = (DIF - DEA) * 2
"""
import pandas as pd
import pytest


class TestMACD:
    def test_macd_columns(self, sample_kline):
        from indicators.macd import MACD
        result = MACD().compute(sample_kline.copy())
        for col in ("dif", "dea", "hist"):
            assert col in result.columns, f"应该新增 {col} 列"
        assert len(result) == len(sample_kline)

    def test_macd_does_not_mutate_input(self, sample_kline):
        from indicators.macd import MACD
        original_cols = list(sample_kline.columns)
        MACD().compute(sample_kline)
        assert list(sample_kline.columns) == original_cols

    def test_macd_known_value(self, sample_kline):
        """第 30 行以后，MACD 三值应都已计算（不再全 NaN）"""
        from indicators.macd import MACD
        result = MACD().compute(sample_kline.copy())
        assert not result["dif"].iloc[30:].isna().all()
        assert not result["dea"].iloc[30:].isna().all()

    def test_macd_hist_is_2x_diff_minus_dea(self, sample_kline):
        from indicators.macd import MACD
        result = MACD().compute(sample_kline.copy())
        # 在都不为 NaN 的位置验证 hist = (dif - dea) * 2
        mask = result["dif"].notna() & result["dea"].notna()
        expected_hist = (result["dif"][mask] - result["dea"][mask]) * 2
        pd.testing.assert_series_equal(
            result["hist"][mask], expected_hist, check_names=False
        )

    def test_macd_custom_params(self, sample_kline):
        from indicators.macd import MACD
        result = MACD(fast=5, slow=10, signal=3).compute(sample_kline.copy())
        assert "dif" in result.columns
        # 短窗口更快有值
        assert not result["dif"].iloc[10:].isna().all()

    def test_macd_metadata(self):
        from indicators.macd import MACD
        ind = MACD()
        assert ind.name == "macd"
        assert ind.params["fast"] == 12
        assert ind.params["slow"] == 26
        assert ind.params["signal"] == 9
