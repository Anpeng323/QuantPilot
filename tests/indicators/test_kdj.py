"""
TDD: KDJ 随机指标
- RSV = (close - low_n_min) / (high_n_max - low_n_min) * 100
- K = 2/3 * K_prev + 1/3 * RSV   (init K=50)
- D = 2/3 * D_prev + 1/3 * K      (init D=50)
- J = 3K - 2D
"""
import math
import pandas as pd
import pytest


def _ref_kdj(df: pd.DataFrame, n: int = 9):
    """独立参考实现（递归），用于交叉验证生产代码。"""
    low_min = df["low"].rolling(n, min_periods=n).min()
    high_max = df["high"].rolling(n, min_periods=n).max()
    denom = (high_max - low_min).replace(0, float("nan"))
    rsv = (df["close"] - low_min) / denom * 100
    k_vals, d_vals = [], []
    k_prev, d_prev = 50.0, 50.0
    for x in rsv:
        if pd.isna(x):
            kc, dc = k_prev, d_prev
        else:
            kc = (2 * k_prev + x) / 3.0
            dc = (2 * d_prev + kc) / 3.0
        k_vals.append(kc)
        d_vals.append(dc)
        k_prev, d_prev = kc, dc
    k = pd.Series(k_vals, index=df.index)
    d = pd.Series(d_vals, index=df.index)
    j = 3 * k - 2 * d
    return k, d, j


class TestKDJ:
    def test_columns_and_length(self, sample_kline):
        from indicators.kdj import KDJ
        res = KDJ().compute(sample_kline.copy())
        for c in ("kdj_k", "kdj_d", "kdj_j"):
            assert c in res.columns, f"缺少列 {c}"
        assert len(res) == len(sample_kline)

    def test_no_mutation(self, sample_kline):
        from indicators.kdj import KDJ
        cols = list(sample_kline.columns)
        KDJ().compute(sample_kline)
        assert list(sample_kline.columns) == cols

    def test_initial_kd_is_50(self, sample_short_kline):
        from indicators.kdj import KDJ
        res = KDJ(n=9).compute(sample_short_kline.copy())
        assert math.isclose(res["kdj_k"].iloc[0], 50.0, abs_tol=1e-9)
        assert math.isclose(res["kdj_d"].iloc[0], 50.0, abs_tol=1e-9)

    def test_hand_computed_first_valid(self):
        """n=3 的极小样例，前 3 行手工可算。"""
        df = pd.DataFrame({
            "open":  [10, 10, 11, 12],
            "high":  [11, 12, 12.5, 13],
            "low":   [9, 9.5, 10.5, 11.5],
            "close": [10, 11, 12, 13],
        })
        from indicators.kdj import KDJ
        res = KDJ(n=3).compute(df)
        # row2 是第一个能算 RSV 的位置
        # RSV = (12-9)/(12.5-9)*100 = 85.7143
        # K = (2*50 + 85.7143)/3 = 61.9048
        # D = (2*50 + 61.9048)/3 = 53.9683
        # J = 3K - 2D = 77.7778
        assert math.isclose(res["kdj_k"].iloc[2], 61.9048, abs_tol=1e-3)
        assert math.isclose(res["kdj_d"].iloc[2], 53.9683, abs_tol=1e-3)
        assert math.isclose(res["kdj_j"].iloc[2], 77.7778, abs_tol=1e-3)

    def test_cross_check_reference(self, sample_kline):
        from indicators.kdj import KDJ
        res = KDJ().compute(sample_kline.copy())
        rk, rd, rj = _ref_kdj(sample_kline.copy(), n=9)
        pd.testing.assert_series_equal(res["kdj_k"], rk, check_names=False, rtol=1e-6)
        pd.testing.assert_series_equal(res["kdj_d"], rd, check_names=False, rtol=1e-6)
        pd.testing.assert_series_equal(res["kdj_j"], rj, check_names=False, rtol=1e-6)

    def test_bounds(self, sample_kline):
        from indicators.kdj import KDJ
        res = KDJ().compute(sample_kline.copy())
        valid = res["kdj_k"].notna()
        assert (res["kdj_k"][valid] >= -1e-6).all()
        assert (res["kdj_k"][valid] <= 100 + 1e-6).all()
        assert (res["kdj_d"][valid] >= -1e-6).all()
        assert (res["kdj_d"][valid] <= 100 + 1e-6).all()

    def test_metadata(self):
        from indicators.kdj import KDJ
        ind = KDJ()
        assert ind.name == "kdj"
        assert ind.params["n"] == 9

    def test_invalid_period(self):
        from indicators.kdj import KDJ
        with pytest.raises(ValueError):
            KDJ(n=0)
