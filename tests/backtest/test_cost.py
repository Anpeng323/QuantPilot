"""
T11 单元测试 · A股成本模型（对应 spec/Phase1 §4 test_cost.py / AC1.3）
印花税0.05%(仅卖)、佣金万2.5(最低5元)、过户费0.001%(双边)。
"""
from __future__ import annotations

from backtest.cost import CostConfig, compute_cost


def test_sell_100k():
    """10万元卖出：佣金25 + 印花税50 + 过户费1 = 76。"""
    c = compute_cost(CostConfig(), "sell", 10.0, 10000)
    assert c["commission"] == 25.0
    assert c["stamp"] == 50.0
    assert c["transfer"] == 1.0
    assert c["total"] == 76.0


def test_min_commission():
    """小额成交佣金取最低 5 元，且买入不计印花税。"""
    c = compute_cost(CostConfig(), "buy", 1.0, 100)
    assert c["commission"] == 5.0
    assert c["stamp"] == 0.0
    assert c["total"] == 5.001  # 5 + 0(印花税) + 0.001(过户)


def test_buy_no_stamp():
    """买入不收印花税；佣金低于5元时按5元计。"""
    c = compute_cost(CostConfig(), "buy", 10.0, 1000)
    assert c["stamp"] == 0.0
    assert c["commission"] == 5.0
