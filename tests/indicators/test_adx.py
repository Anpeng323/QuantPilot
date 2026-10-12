"""
TDD: ADX 平均趋向指数（趋势强度）
- +DM = max(H-H_prev, 0) if (H-H_prev)>(L_prev-L) else 0
- -DM = max(L_prev-L, 0) if (L_prev-L)>(H-H_prev) else 0
- TR  = max(H-L, |H-YC|, |L-YC|)
- +DI / -DI = 100 * Wilder(+DM/TR) / Wilder(TR)
- DX  = 100 * |+DI - -DI| / (+DI + -DI)
- ADX = Wilder(DX, n)
输出列：adx / plus_di / minus_di
"""
import numpy as np
import pandas as pd
import pytest


def _wilder(series: pd.Series, n: int) -> pd.Series:
    """Wilder 平滑：前 n 个做 SMA 种子，之后递归 alpha=1/n。"""
    vals = series.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    if len(vals) < n:
        return pd.Series(out, index=series.index)
    seed = float(np.nanmean(vals[:n]))
    out[n - 1] = seed
    prev = seed
    for i in range(n, len(vals)):
        prev = (prev * (n - 1) + vals[i]) / n
        out[i] = prev
    return pd.Series(out, index=series.index)


def _ref_adx(df: pd.DataFrame, n: int = 14):
    high, low, close = df["high"], df["low"], df["close"]
    prev_high, prev_low, prev_close = high.shift(1), low.shift(1), close.shift(1)
    up = high - prev_high
    down = prev_low - low
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)
    atr = _wilder(tr, n)
    pdm = _wilder(plus_dm, n)
    mdm = _wilder(minus_dm, n)
    denom = atr.replace(0, np.nan)
    plus_di = 100 * pdm / denom
    minus_di = 100 * mdm / denom
    dx_denom = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / dx_denom
    adx = _wilder(dx, n)
    return adx, plus_di, minus_di


class TestADX:
    def test_columns_and_length(self, sample_kline):
        from indicators.adx import ADX
        res = ADX().compute(sample_kline.copy())
        for c in ("adx", "plus_di", "minus_di"):
            assert c in res.columns, f"缺少列 {c}"
        assert len(res) == len(sample_kline)

    def test_no_mutation(self, sample_kline):
        from indicators.adx import ADX
        cols = list(sample_kline.columns)
        ADX().compute(sample_kline)
        assert list(sample_kline.columns) == cols

    def test_monotonic_uptrend_high_adx(self):
        """持续上涨 -> ADX 应显著 > 0 且 +DI 远大于 -DI。"""
        n = 60
        close = np.linspace(10, 50, n)
        df = pd.DataFrame({
            "open": close - 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
        })
        from indicators.adx import ADX
        res = ADX(n=14).compute(df)
        tail = res.dropna()
        assert (tail["adx"] > 50).any()
        assert (tail["plus_di"] > tail["minus_di"]).all()

    def test_flat_price_adx_near_zero(self):
        n = 60
        close = np.full(n, 10.0)
        df = pd.DataFrame({
            "open": close, "high": close, "low": close, "close": close,
        })
        from indicators.adx import ADX
        res = ADX(n=14).compute(df)
        tail = res.dropna()
        # 无波动 -> TR=0 -> DI=0 -> DX=0 -> ADX 接近 0
        assert (tail["adx"].abs() < 1e-6).all()

    def test_di_bounds(self, sample_kline):
        from indicators.adx import ADX
        res = ADX().compute(sample_kline.copy())
        v = res["plus_di"].dropna()
        assert (v >= -1e-6).all() and (v <= 100 + 1e-6).all()
        v2 = res["minus_di"].dropna()
        assert (v2 >= -1e-6).all() and (v2 <= 100 + 1e-6).all()

    def test_cross_check_reference(self, sample_kline):
        from indicators.adx import ADX
        res = ADX().compute(sample_kline.copy())
        radx, rpdi, rmdi = _ref_adx(sample_kline.copy(), n=14)
        pd.testing.assert_series_equal(res["adx"], radx, check_names=False, rtol=1e-6)
        pd.testing.assert_series_equal(res["plus_di"], rpdi, check_names=False, rtol=1e-6)
        pd.testing.assert_series_equal(res["minus_di"], rmdi, check_names=False, rtol=1e-6)

    def test_metadata(self):
        from indicators.adx import ADX
        ind = ADX()
        assert ind.name == "adx"
        assert ind.params["n"] == 14

    def test_invalid_period(self):
        from indicators.adx import ADX
        with pytest.raises(ValueError):
            ADX(n=0)
