"""
RSI（Relative Strength Index，相对强弱指标）。

公式：
- delta = close.diff()
- gain = max(delta, 0)
- loss = abs(min(delta, 0))
- avg_gain = gain.rolling(period).mean()   用 Wilder 平滑
- avg_loss = loss.rolling(period).mean()
- RS = avg_gain / avg_loss
- RSI = 100 - 100 / (1 + RS)

使用 Wilder 平滑（与 EMA 等价：alpha = 1/period）。
"""
from __future__ import annotations
import pandas as pd
from .base import Indicator, ParamSpec


class RSI(Indicator):
    """相对强弱指数"""

    def __init__(self, period: int = 14, **kwargs):
        if period <= 0:
            raise ValueError("period 必须 > 0")
        super().__init__(period=period, **kwargs)
        self.period = period

    @property
    def name(self) -> str:
        return f"rsi{self.period}"

    @classmethod
    def _default_params(cls) -> dict:
        return {"period": 14}

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        return [ParamSpec("period", "int", 14, min=2, max=100, description="窗口")]

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        if "close" not in df.columns:
            raise KeyError("DataFrame 必须包含 close 列")
        result = df.copy()
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        # Wilder 平滑（用 alpha=1/period 的 EMA）
        avg_gain = gain.ewm(alpha=1 / self.period, adjust=False, min_periods=self.period).mean()
        avg_loss = loss.ewm(alpha=1 / self.period, adjust=False, min_periods=self.period).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-12)  # 防 /0
        rsi = 100 - 100 / (1 + rs)
        result[self.name] = rsi
        return result
