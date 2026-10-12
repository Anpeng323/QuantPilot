"""
技术指标计算模块。

设计为可注册到 registry，每个指标：
- name: 指标名（用于 lookup）
- params: 参数字典
- compute(df) -> DataFrame：不原地修改入参，返回加列的 DataFrame

新增指标：
1. 在 indicators/ 下新建模块，实现 Indicator 协议
2. 在 registry.py 里 import 并加入 REGISTRY
"""
from .base import Indicator, ParamSpec
from .ma import MA
from .macd import MACD
from .boll import BOLL
from .rsi import RSI
from .kdj import KDJ
from .arbr import ARBR
from .adx import ADX
from .stochrsi import StochRSI

__all__ = [
    "Indicator", "ParamSpec",
    "MA", "MACD", "BOLL", "RSI",
    "KDJ", "ARBR", "ADX", "StochRSI",
]
