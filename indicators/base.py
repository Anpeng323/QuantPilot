"""
Indicator 抽象基类。

所有指标都继承 Indicator，实现：
- name: 类属性，唯一标识（也是 DataFrame 加列的前缀）
- compute(df, **params) -> DataFrame
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, Any
from dataclasses import dataclass
import pandas as pd


@dataclass
class ParamSpec:
    """参数规格（可选，用于 UI 生成表单）"""
    name: str
    type: str       # 'int' / 'float' / 'str'
    default: Any
    min: float = None
    max: float = None
    description: str = ""


class Indicator(ABC):
    """指标抽象类"""

    def __init__(self, **params):
        self.params = self._default_params()
        self.params.update(params)

    @property
    @abstractmethod
    def name(self) -> str:
        """指标名（用于加列前缀 / 标识）"""

    @classmethod
    def _default_params(cls) -> Dict[str, Any]:
        """子类可覆盖：返回默认参数字典"""
        return {}

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        """子类可覆盖：返回参数规格列表（用于生成 UI）"""
        return []

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算指标。
        - 入参：df 必含 close 列（其他列：open/high/low/volume 可选）
        - 出参：df 副本 + 新增列
        - 不原地修改入参
        """
