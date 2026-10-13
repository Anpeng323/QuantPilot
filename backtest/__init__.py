"""
规则回测引擎包（Phase 1）。

公开 API：
- run_backtest(req)        从数据库加载行情并回测
- run_backtest_df(df, req) 直接对给定 DataFrame 回测（便于测试/复用）
- BacktestRequest          请求数据类
- CostConfig               成本模型配置
- SignalParseError         DSL 解析错误（映射到 HTTP 400）
- BacktestError            运行时错误（数据不足/缺失率过高等）
"""
from .errors import BacktestError, SignalParseError
from .cost import CostConfig, compute_cost
from .engine import BacktestRequest, run_backtest, run_backtest_df

__all__ = [
    "BacktestError", "SignalParseError", "CostConfig", "compute_cost",
    "BacktestRequest", "run_backtest", "run_backtest_df",
]
