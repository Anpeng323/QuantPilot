"""合规层：投研内容的免责声明与禁用词校验（spec §6.5）。"""
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


def scan(text: str) -> dict:
    """扫描禁用词，返回 {ok, hits}。"""
    hits = [w for w in BAN_WORDS if w in (text or "")]
    return {"ok": len(hits) == 0, "hits": hits}


def enforce(text: str) -> dict:
    """合规校验：返回 {text, ok, hits, note}。命中时附加提示。"""
    res = scan(text)
    note = ""
    if not res["ok"]:
        note = "⚠️ 内容触发合规拦截，已调整表述，仅供参考。"
    return {"text": text or "", "ok": res["ok"], "hits": res["hits"], "note": note}


def disclaimer_text() -> str:
    return DISCLAIMER
