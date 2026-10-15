"""
绩效指标计算（Phase 1 / T3）。

输入权益曲线（每日收盘市值）与基准曲线，计算：
total_return / annual_return / max_drawdown / sharpe / win_rate / excess_return
无风险利率默认 0.025（≈10Y 国债）。
"""
from __future__ import annotations
import math


def compute_metrics(equity: list[float], benchmark: list[float | None] | None,
                    trades: list[dict], rf: float = 0.025) -> dict:
    n = len(equity)
    zero = {
        "total_return": 0.0, "annual_return": 0.0, "max_drawdown": 0.0,
        "sharpe": 0.0, "win_rate": 0.0, "excess_return": 0.0,
    }
    if n < 2 or equity[0] == 0:
        return zero

    total_return = equity[-1] / equity[0] - 1.0

    # 年化（复利口径）
    years = n / 252.0
    annual_return = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 else 0.0

    # 日收益序列（夏普按超额收益口径：扣掉无风险利率）
    rets = [equity[i] / equity[i - 1] - 1.0 for i in range(1, n)]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    std = math.sqrt(var) if var > 0 else 0.0
    excess_daily = mean - rf / 252.0
    sharpe = (excess_daily / std) * math.sqrt(252.0) if std > 0 else 0.0

    # 最大回撤
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = v / peak - 1.0
        if dd < mdd:
            mdd = dd

    # 胜率（仅统计已平仓的卖出交易）
    closed = [t for t in trades if "pnl" in t]
    win_rate = (sum(1 for t in closed if t["pnl"] > 0) / len(closed)) if closed else 0.0

    # 超额收益（基准）
    excess = 0.0
    if benchmark:
        first = next((v for v in benchmark if v is not None), None)
        last = next((v for v in reversed(benchmark) if v is not None), None)
        if first not in (None, 0):
            bench_total = last / first - 1.0
            excess = total_return - bench_total

    return {
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6),
        "max_drawdown": round(mdd, 6),
        "sharpe": round(sharpe, 4),
        "win_rate": round(win_rate, 4),
        "excess_return": round(excess, 6),
    }
