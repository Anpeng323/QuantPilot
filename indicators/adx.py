"""
ADX（Average Directional Index，平均趋向指数）。

计算链（窗口 n，默认 14）：
- +DM = max(H-H_prev, 0)  if (H-H_prev) > (L_prev-L)  else 0
- -DM = max(L_prev-L, 0)  if (L_prev-L) > (H-H_prev)  else 0
- TR  = max(H-L, |H-YC|, |L-YC|)
- +DI / -DI = 100 * Wilder(+DM) / Wilder(TR)
- DX  = 100 * |+DI - -DI| / (+DI + -DI)
- ADX = Wilder(DX, n)

Wilder 平滑：前 n 个做 SMA 种子，之后 alpha=1/n 递归。
输出列：adx / plus_di / minus_di
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from .base import Indicator, ParamSpec


def _wilder(series: pd.Series, n: int) -> pd.Series:
    """Wilder 平滑：前 n 值做 SMA 种子，之后递归 alpha=1/n。"""
    vals = series.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    if len(vals) < n:
        return pd.Series(out, index=series.index)
    window = vals[:n]
    seed = float(np.nanmean(window)) if np.isfinite(window).any() else float("nan")
    out[n - 1] = seed
    prev = seed
    for i in range(n, len(vals)):
        prev = (prev * (n - 1) + vals[i]) / n
        out[i] = prev
    return pd.Series(out, index=series.index)


class ADX(Indicator):
    """平均趋向指数 ADX"""

    def __init__(self, n: int = 14, **kwargs):
        if n <= 0:
            raise ValueError(f"n 必须 > 0，当前={n}")
        super().__init__(n=n, **kwargs)
        self.n = n

    @property
    def name(self) -> str:
        return "adx"

    @classmethod
    def _default_params(cls) -> dict:
        return {"n": 14}

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        return [ParamSpec("n", "int", 14, min=2, max=100, description="平滑窗口")]

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in ("high", "low", "close"):
            if col not in df.columns:
                raise KeyError(f"DataFrame 必须包含 {col} 列")
        result = df.copy()
        high, low, close = df["high"], df["low"], df["close"]
        prev_high, prev_low, prev_close = high.shift(1), low.shift(1), close.shift(1)

        up = high - prev_high
        down = prev_low - low
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        tr = pd.concat(
            [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
        ).max(axis=1)

        plus_dm = pd.Series(plus_dm, index=df.index)
        minus_dm = pd.Series(minus_dm, index=df.index)
        atr = _wilder(tr, self.n)
        pdm = _wilder(plus_dm, self.n)
        mdm = _wilder(minus_dm, self.n)

        denom = atr.replace(0, np.nan)
        plus_di = 100 * pdm / denom
        minus_di = 100 * mdm / denom
        dx_denom = (plus_di + minus_di).replace(0, np.nan)
        dx = 100 * (plus_di - minus_di).abs() / dx_denom
        adx = _wilder(dx, self.n)

        result["adx"] = adx
        result["plus_di"] = plus_di
        result["minus_di"] = minus_di
        return result
