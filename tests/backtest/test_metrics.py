"""
T11 单元测试 · 绩效指标（对应 spec/Phase1 §4 test_metrics.py）
total_return / annual_return / max_drawdown / sharpe / win_rate / excess_return。
"""
from __future__ import annotations

from backtest.metrics import compute_metrics


def test_known_curve():
    """手工权益曲线：100→120→90→110。"""
    m = compute_metrics([100.0, 120.0, 90.0, 110.0], None, [])
    assert m["total_return"] == 0.1
    assert m["max_drawdown"] == -0.25  # 120 回撤到 90
    assert m["annual_return"] > 0
    assert m["sharpe"] > 0


def test_flat_curve():
    """无波动曲线：收益/回撤/夏普均为 0。"""
    m = compute_metrics([100.0, 100.0, 100.0], None, [])
    assert m["total_return"] == 0.0
    assert m["max_drawdown"] == 0.0
    assert m["sharpe"] == 0.0


def test_win_rate():
    """已平仓交易胜率：1 胜 1 负 → 0.5；无平仓 → 0。"""
    m = compute_metrics([100.0, 110.0], None, [{"pnl": 10.0}, {"pnl": -5.0}])
    assert m["win_rate"] == 0.5
    m2 = compute_metrics([100.0, 110.0], None, [])
    assert m2["win_rate"] == 0.0


def test_excess_return():
    """基准 1000→1050(+5%)，策略 +10% → 超额 +5%。"""
    m = compute_metrics([100.0, 110.0], [1000.0, 1050.0], [])
    assert m["excess_return"] == 0.05
    m2 = compute_metrics([100.0, 110.0], None, [])
    assert m2["excess_return"] == 0.0


def test_insufficient_data_returns_zero():
    """权益曲线不足 2 点 → 全零，不崩溃。"""
    assert compute_metrics([100.0], None, [])["total_return"] == 0.0
