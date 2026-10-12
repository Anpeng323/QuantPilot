"""
指标注册表。

新增指标只需在 indicators/ 下实现 Indicator 子类，
然后在这里 import 并加入 REGISTRY。
"""
from __future__ import annotations
from typing import Dict, Type, List, Any
from .base import Indicator
from .ma import MA
from .macd import MACD
from .boll import BOLL
from .rsi import RSI
from .kdj import KDJ
from .arbr import ARBR
from .adx import ADX
from .stochrsi import StochRSI


REGISTRY: Dict[str, Type[Indicator]] = {
    "ma":       MA,
    "macd":     MACD,
    "boll":     BOLL,
    "rsi":      RSI,
    "kdj":      KDJ,
    "arbr":     ARBR,
    "adx":      ADX,
    "stochrsi": StochRSI,
}


def get_indicator(name: str, **params: Any) -> Indicator:
    """通过名字获取指标实例"""
    if name not in REGISTRY:
        raise KeyError(f"未知指标: {name}。可用: {list(REGISTRY.keys())}")
    cls = REGISTRY[name]
    return cls(**params)


def list_indicators() -> List[str]:
    """列出所有注册的指标名"""
    return list(REGISTRY.keys())
