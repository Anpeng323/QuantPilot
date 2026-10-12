"""
MA（移动平均线）。

简单移动平均：
- MA(n).compute(df) -> df + ma{n} 列
"""
from __future__ import annotations
import pandas as pd
from .base import Indicator, ParamSpec


class MA(Indicator):
    """简单移动平均线"""

    def __init__(self, period: int = 5, **kwargs):
        if period <= 0:
            raise ValueError(f"period 必须 > 0，当前={period}")
        super().__init__(period=period, **kwargs)
        self.period = period

    @property
    def name(self) -> str:
        return f"ma{self.period}"

    @classmethod
    def _default_params(cls) -> dict:
        return {"period": 5}

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        return [ParamSpec("period", "int", 5, min=2, max=250, description="窗口大小")]

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        if "close" not in df.columns:
            raise KeyError("DataFrame 必须包含 close 列")
        result = df.copy()
        result[self.name] = df["close"].rolling(window=self.period, min_periods=self.period).mean()
        return result
