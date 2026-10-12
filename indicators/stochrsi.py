"""
StochRSI（随机相对强弱指标）。

计算链：
- 先算 Wilder RSI(close, rsi_period)
- StochRSI = (RSI - min(RSI, stoch_period)) / (max(RSI, stoch_period) - min(RSI, stoch_period)) * 100
- %D = StochRSI 的 SMA(smooth)

RSI 为常数的区间（max==min）分母为 0，返回 NaN。
输出列：stochrsi (%K 原始) / stochrsi_d (%D 平滑)
"""
from __future__ import annotations
import pandas as pd
from .base import Indicator, ParamSpec


class StochRSI(Indicator):
    """随机相对强弱指标 StochRSI"""

    def __init__(self, rsi_period: int = 14, stoch_period: int = 14, smooth: int = 3, **kwargs):
        if rsi_period <= 0:
            raise ValueError(f"rsi_period 必须 > 0，当前={rsi_period}")
        if stoch_period <= 0:
            raise ValueError(f"stoch_period 必须 > 0，当前={stoch_period}")
        if smooth <= 0:
            raise ValueError(f"smooth 必须 > 0，当前={smooth}")
        super().__init__(rsi_period=rsi_period, stoch_period=stoch_period, smooth=smooth, **kwargs)
        self.rsi_period = rsi_period
        self.stoch_period = stoch_period
        self.smooth = smooth

    @property
    def name(self) -> str:
        return "stochrsi"

    @classmethod
    def _default_params(cls) -> dict:
        return {"rsi_period": 14, "stoch_period": 14, "smooth": 3}

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        return [
            ParamSpec("rsi_period", "int", 14, min=2, max=100, description="RSI 窗口"),
            ParamSpec("stoch_period", "int", 14, min=2, max=100, description="Stoch 窗口"),
            ParamSpec("smooth", "int", 3, min=1, max=50, description="%D 平滑窗口"),
        ]

    def _wilder_rsi(self, close: pd.Series) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / self.rsi_period, adjust=False, min_periods=self.rsi_period).mean()
        avg_loss = loss.ewm(alpha=1 / self.rsi_period, adjust=False, min_periods=self.rsi_period).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-12)
        return 100 - 100 / (1 + rs)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        if "close" not in df.columns:
            raise KeyError("DataFrame 必须包含 close 列")
        result = df.copy()
        rsi = self._wilder_rsi(df["close"])
        lo = rsi.rolling(self.stoch_period).min()
        hi = rsi.rolling(self.stoch_period).max()
        denom = (hi - lo).replace(0, float("nan"))
        stoch = (rsi - lo) / denom * 100
        d = stoch.rolling(self.smooth).mean()
        result["stochrsi"] = stoch
        result["stochrsi_d"] = d
        return result
