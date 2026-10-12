"""
TDD: ARBR 人气意愿指标（情绪指标）
- AR = Σ(H-O) / Σ(O-L) * 100         (窗口 n)
- BR = Σ(H-YC) / Σ(YC-L) * 100       (YC = 前收盘，首行回退为自身收盘)
分母为 0 时返回 NaN（避免除零）。
"""
import math
import pandas as pd
import pytest


def _ref_arbr(df: pd.DataFrame, n: int = 26):
    """独立参考实现，用于交叉验证。"""
    yc = df["close"].shift(1).fillna(df["close"])
    ho = (df["high"] - df["open"]).clip(lower=0)
    ol = (df["open"] - df["low"]).clip(lower=0)
    hy = (df["high"] - yc).clip(lower=0)
    yl = (yc - df["low"]).clip(lower=0)
    ar = ho.rolling(n).sum() / ol.rolling(n).sum().replace(0, float("nan")) * 100
    br = hy.rolling(n).sum() / yl.rolling(n).sum().replace(0, float("nan")) * 100
    return ar, br


class TestARBR:
    def test_columns_and_length(self, sample_kline):
        from indicators.arbr import ARBR
        res = ARBR().compute(sample_kline.copy())
        assert "ar" in res.columns and "br" in res.columns
        assert len(res) == len(sample_kline)

    def test_no_mutation(self, sample_kline):
        from indicators.arbr import ARBR
        cols = list(sample_kline.columns)
        ARBR().compute(sample_kline)
        assert list(sample_kline.columns) == cols

    def test_hand_computed(self):
        df = pd.DataFrame({
            "open":  [10, 10, 11, 12],
            "high":  [11, 12, 12.5, 13],
            "low":   [9, 9.5, 10.5, 11.5],
            "close": [10, 11, 12, 13],
        })
        from indicators.arbr import ARBR
        res = ARBR(n=3).compute(df)
        # row2: Σ(H-O)=4.5, Σ(O-L)=2.0 -> AR=225
        #       Σ(H-YC)=4.5, Σ(YC-L)=2.0 -> BR=225
        assert math.isclose(res["ar"].iloc[2], 225.0, abs_tol=1e-6)
        assert math.isclose(res["br"].iloc[2], 225.0, abs_tol=1e-6)

    def test_div_by_zero_returns_nan(self):
        """所有 O==L 的行情，OL 求和为 0 -> AR 应为 NaN。"""
        df = pd.DataFrame({
            "open":  [10, 10, 11],
            "high":  [11, 12, 12.5],
            "low":   [10, 10, 11],
            "close": [10.5, 11, 12],
        })
        from indicators.arbr import ARBR
        res = ARBR(n=3).compute(df)
        assert res["ar"].isna().all()
        assert res["br"].notna().any()

    def test_cross_check_reference(self, sample_kline):
        from indicators.arbr import ARBR
        res = ARBR().compute(sample_kline.copy())
        rar, rbr = _ref_arbr(sample_kline.copy(), n=26)
        pd.testing.assert_series_equal(res["ar"], rar, check_names=False, rtol=1e-6)
        pd.testing.assert_series_equal(res["br"], rbr, check_names=False, rtol=1e-6)

    def test_metadata(self):
        from indicators.arbr import ARBR
        ind = ARBR()
        assert ind.name == "arbr"
        assert ind.params["n"] == 26

    def test_invalid_period(self):
        from indicators.arbr import ARBR
        with pytest.raises(ValueError):
            ARBR(n=0)
