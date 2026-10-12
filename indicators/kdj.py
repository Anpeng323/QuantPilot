"""
KDJ（随机指标）。

公式：
- RSV = (close - low_n_min) / (high_n_max - low_n_min) * 100
- K = 2/3 * K_prev + 1/3 * RSV   (init K=50)
- D = 2/3 * D_prev + 1/3 * K      (init D=50)
- J = 3K - 2D

输出列：kdj_k / kdj_d / kdj_j
"""
from __future__ import annotations
import pandas as pd
from .base import Indicator, ParamSpec


class KDJ(Indicator):
    """随机指标 KDJ"""

    def __init__(self, n: int = 9, **kwargs):
        if n <= 0:
            raise ValueError(f"n 必须 > 0，当前={n}")
        super().__init__(n=n, **kwargs)
        self.n = n

    @property
    def name(self) -> str:
        return "kdj"

    @classmethod
    def _default_params(cls) -> dict:
        return {"n": 9}

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        return [ParamSpec("n", "int", 9, min=2, max=100, description="RSV 窗口")]

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in ("close", "high", "low"):
            if col not in df.columns:
                raise KeyError(f"DataFrame 必须包含 {col} 列")
        result = df.copy()
        low_min = df["low"].rolling(self.n, min_periods=self.n).min()
        high_max = df["high"].rolling(self.n, min_periods=self.n).max()
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

        result["kdj_k"] = pd.Series(k_vals, index=df.index)
        result["kdj_d"] = pd.Series(d_vals, index=df.index)
        result["kdj_j"] = 3 * result["kdj_k"] - 2 * result["kdj_d"]
        return result
