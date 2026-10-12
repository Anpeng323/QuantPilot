"""
BOLL（布林带）。

公式：
- mid = MA(close, period)
- std = close.rolling(period).std(ddof=1)   样本标准差
- up = mid + k * std
- down = mid - k * std

输出列：boll_mid / boll_up / boll_down
"""
from __future__ import annotations
import pandas as pd
from .base import Indicator, ParamSpec


class BOLL(Indicator):
    """布林带"""

    def __init__(self, period: int = 20, k: float = 2.0, **kwargs):
        if period <= 0:
            raise ValueError("period 必须 > 0")
        if k <= 0:
            raise ValueError("k 必须 > 0")
        super().__init__(period=period, k=k, **kwargs)
        self.period = period
        self.k = k

    @property
    def name(self) -> str:
        return "boll"

    @classmethod
    def _default_params(cls) -> dict:
        return {"period": 20, "k": 2.0}

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        return [
            ParamSpec("period", "int",   20,  min=5, max=100, description="窗口大小"),
            ParamSpec("k",      "float", 2.0, min=1, max=4,   description="标准差倍数"),
        ]

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        if "close" not in df.columns:
            raise KeyError("DataFrame 必须包含 close 列")
        result = df.copy()
        mid = df["close"].rolling(window=self.period, min_periods=self.period).mean()
        std = df["close"].rolling(window=self.period, min_periods=self.period).std(ddof=1)
        result["boll_mid"]   = mid
        result["boll_up"]    = mid + self.k * std
        result["boll_down"]  = mid - self.k * std
        return result
