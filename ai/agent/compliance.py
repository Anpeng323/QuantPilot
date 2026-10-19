"""合规层：投研内容的免责声明与禁用词改写（spec §6.5）。

相比旧版「仅加提示」，本版在命中禁用词时**主动改写**为中性投研表述，
并标记该回答是否为「投资建议」，便于前端展示「AI 观点，仅供参考」卡片。
"""
from __future__ import annotations

DISCLAIMER = (
    "本内容为 AI 基于公开数据的投研观点整理，仅供参考，不构成任何投资建议，"
    "不承诺收益，投资有风险。"
)

# 禁止词库：命中即触发合规拦截（荐股 / 收益承诺 / 保本表述）
BAN_WORDS = [
    "买入", "卖出", "建仓", "清仓", "目标价", "看涨至", "必涨", "稳赚",
    "保本", "无风险", "收益保证", "推荐购买", "建议购买", "逢低买入",
    "逢高卖出", "强烈推荐", "闭眼买",
]

# 中性化改写映射（命中禁用词时替换，避免直接给出交易指令 / 收益承诺）
_REWRITE = [
    ("强烈推荐", "建议关注"), ("推荐购买", "可关注"), ("建议购买", "可关注"),
    ("闭眼买", "谨慎关注"), ("逢低买入", "回调时可关注"), ("逢高卖出", "走强后可注意风险"),
    ("买入", "关注"), ("建仓", "关注"), ("清仓", "注意风险"), ("卖出", "注意风险"),
    ("目标价", "参考区间（非目标价）"), ("看涨至", "若突破"),
    ("必涨", "存在上行可能"), ("稳赚", "存在盈利机会"), ("保本", "注意回撤风险"),
    ("无风险", "注意风险"), ("收益保证", "收益不确定"),
]

# 投资建议判定辅助词（用于标记 investment_view）
_ADVICE_HINTS = ["建议", "推荐", "关注", "可配", "可布局", "值得", "看好", "谨慎", "回避"]


def scan(text: str) -> dict:
    """扫描禁用词，返回 {ok, hits}。"""
    hits = [w for w in BAN_WORDS if w in (text or "")]
    return {"ok": len(hits) == 0, "hits": hits}


def enforce(text: str) -> dict:
    """合规校验：命中时**改写**为中性表述，并标记是否投资建议。

    返回 {text, ok, hits, note, investment_view}。
    """
    text = text or ""
    hits = [w for w in BAN_WORDS if w in text]
    if hits:
        for bad, good in _REWRITE:
            if bad in text:
                text = text.replace(bad, good)
        # 改写后可能仍有残留禁用词（如多字词组），再清一遍
        for bad, good in _REWRITE:
            if bad in text:
                text = text.replace(bad, good)
        note = "⚠️ 内容触发合规拦截，已调整为中性投研表述，仅供参考。"
    else:
        note = ""
    ok = not any(w in text for w in BAN_WORDS)
    investment_view = bool(hits) or any(h in text for h in _ADVICE_HINTS)
    return {"text": text, "ok": ok, "hits": hits, "note": note,
            "investment_view": investment_view}


def disclaimer_text() -> str:
    return DISCLAIMER
