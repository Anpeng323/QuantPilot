"""回测模块自定义异常。"""


class SignalParseError(ValueError):
    """信号 DSL 解析失败（映射到 HTTP 400）。"""


class BacktestError(Exception):
    """回测运行时错误：数据不足、缺失率过高等（非代码缺陷）。"""
