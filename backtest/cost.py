"""
A股交易成本模型（Phase 1 / T2）。

费用构成（仅手续费，滑点由引擎在成交价上处理）：
- 佣金：成交金额 × commission，单笔最低 min_commission（默认万2.5 / 5元）
- 印花税：仅卖出，成交金额 × stamp（默认 0.05%）
- 过户费：双边，成交金额 × transfer（默认 0.001%）
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class CostConfig:
    commission: float = 0.00025
    min_commission: float = 5.0
    stamp: float = 0.0005
    transfer: float = 0.00001
    slippage: float = 0.0  # 滑点（由引擎应用到成交价，不在此处计费）


def compute_cost(cfg: CostConfig, side: str, price: float, quantity: int) -> dict:
    """
    计算单笔交易费用明细。
    side: 'buy' | 'sell'
    返回 {commission, stamp, transfer, total}
    """
    amount = price * quantity
    commission = max(amount * cfg.commission, cfg.min_commission)
    stamp = amount * cfg.stamp if side == "sell" else 0.0
    transfer = amount * cfg.transfer
    total = commission + stamp + transfer
    return {
        "commission": round(commission, 4),
        "stamp": round(stamp, 4),
        "transfer": round(transfer, 4),
        "total": round(total, 4),
    }
