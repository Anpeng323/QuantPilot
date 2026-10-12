"""
MACD（指数平滑异同移动平均线）。

公式：
- EMA(n) = (Close - EMA_prev) * k + EMA_prev   k = 2/(n+1)
- DIF = EMA(close, 12) - EMA(close, 26)
- DEA = EMA(DIF, 9)
- HIST = (DIF - DEA) * 2

输出列：dif / dea / hist
"""
from __future__ import annotations
import pandas as pd
from .base import Indicator, ParamSpec


def _ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均（pandas 实现）"""
    return series.ewm(span=period, adjust=False).mean()


class MACD(Indicator):
    """MACD 指标"""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9, **kwargs):
        if fast <= 0 or slow <= 0 or signal <= 0:
            raise ValueError("fast/slow/signal 都必须 > 0")
        if fast >= slow:
            raise ValueError(f"fast ({fast}) 必须 < slow ({slow})")
        super().__init__(fast=fast, slow=slow, signal=signal, **kwargs)
        self.fast = fast
        self.slow = slow
        self.signal = signal

    @property
    def name(self) -> str:
        return "macd"

    @classmethod
    def _default_params(cls) -> dict:
        return {"fast": 12, "slow": 26, "signal": 9}

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        return [
            ParamSpec("fast",   "int", 12, min=2, max=60, description="快 EMA 周期"),
            ParamSpec("slow",   "int", 26, min=5, max=120, description="慢 EMA 周期"),
            ParamSpec("signal", "int",  9, min=2, max=60, description="信号线周期"),
        ]

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        if "close" not in df.columns:
            raise KeyError("DataFrame 必须包含 close 列")
        result = df.copy()
        ema_fast = _ema(df["close"], self.fast)
        ema_slow = _ema(df["close"], self.slow)
        result["dif"] = ema_fast - ema_slow
        result["dea"] = _ema(result["dif"], self.signal)
        result["hist"] = (result["dif"] - result["dea"]) * 2
        return result
