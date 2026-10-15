"""意图识别：规则 + 股票代码抽取（spec §3.1）。"""
from __future__ import annotations
from typing import Tuple

from ai.agent.stocks import resolve_stock_codes

# 意图 -> 关键词（命中即归为对应意图）
_INTENT_KEYWORDS = {
    "compare": ["对比", "比较", " versus ", " vs ", "pk", "横向"],
    "backtest": ["回测", "策略", "买入信号", "卖出信号", "signal", "买卖点"],
    "financial": ["财报", "财务", "三表", "利润表", "资产负债表", "现金流量", "季报", "年报", "中报"],
    "quote": ["行情", "股价", "涨跌", "报价", "k线", "k线", "均线", "市值", "pe ", " pb", "换手"],
    "research": ["研究", "分析", "怎么看", "影响", "观点", "建议", "风险点", "估值", "投资逻辑", "护城河"],
}


def classify(question: str) -> Tuple[str, list]:
    """
    返回 (intent, codes)。
    intent ∈ {compare, backtest, financial, quote, research, unknown}
    """
    q = (question or "").lower()
    codes = resolve_stock_codes(question)

    # 多股票 + 对比类词汇 -> 对比
    if len(codes) >= 2 and any(k in question for k in ["对比", "比较", " versus ", " vs ", "和", "与", "pk"]):
        return "compare", codes
    for intent, kws in _INTENT_KEYWORDS.items():
        if any(k in q for k in kws):
            return intent, codes
    return "unknown", codes
