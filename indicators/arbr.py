"""
ARBR（人气意愿指标 / 情绪指标）。

公式（窗口 n）：
- AR = Σ(H-O) / Σ(O-L) * 100
- BR = Σ(H-YC) / Σ(YC-L) * 100        YC = 前收盘（首行回退为自身收盘）

分母为 0 时返回 NaN（避免除零）。
输出列：ar / br
"""
from __future__ import annotations
import pandas as pd
from .base import Indicator, ParamSpec


class ARBR(Indicator):
    """人气意愿指标 ARBR"""

    def __init__(self, n: int = 26, **kwargs):
        if n <= 0:
            raise ValueError(f"n 必须 > 0，当前={n}")
        super().__init__(n=n, **kwargs)
        self.n = n

    @property
    def name(self) -> str:
        return "arbr"

    @classmethod
    def _default_params(cls) -> dict:
        return {"n": 26}

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        return [ParamSpec("n", "int", 26, min=2, max=250, description="窗口大小")]

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in ("open", "high", "low", "close"):
            if col not in df.columns:
                raise KeyError(f"DataFrame 必须包含 {col} 列")
        result = df.copy()
        yc = df["close"].shift(1).fillna(df["close"])
        ho = (df["high"] - df["open"]).clip(lower=0)
        ol = (df["open"] - df["low"]).clip(lower=0)
        hy = (df["high"] - yc).clip(lower=0)
        yl = (yc - df["low"]).clip(lower=0)

        ar = ho.rolling(self.n).sum() / ol.rolling(self.n).sum().replace(0, float("nan")) * 100
        br = hy.rolling(self.n).sum() / yl.rolling(self.n).sum().replace(0, float("nan")) * 100
        result["ar"] = ar
        result["br"] = br
        return result
