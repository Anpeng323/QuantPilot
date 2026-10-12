"""
AI 模型路由器：按 task_type 自动选最合适的 LLM 模型。

默认策略：
- 深度分析任务（财报 / 估值 / DCF / 行业）→ MiniMax-M3（最强模型）
- 轻量任务（分类 / 抽取 / 摘要 / 情绪）→ MiniMax-M3.1-Flash-Preview（快且便宜）
- RAG 长文本问答 → qwen-long（128k 上下文）

可通过 set_config() 覆盖配置。
"""
from __future__ import annotations
import copy
from typing import Any, Dict, Optional, List

from ai import client as llm_client


# 默认路由表
DEFAULT_CONFIG: Dict[str, str] = {
    # 深度任务
    "financial_summary":   "MiniMax-M3",
    "valuation":          "MiniMax-M3",
    "dcf_input":          "MiniMax-M3",
    "industry_analysis":  "MiniMax-M3",
    "macro_analysis":     "MiniMax-M3",
    "comparative":        "MiniMax-M3",
    "risk_scan":          "MiniMax-M3",
    "review":             "MiniMax-M3",
    # 轻量任务
    "sentiment":          "MiniMax-M3.1-Flash-Preview",
    "extraction":         "MiniMax-M3.1-Flash-Preview",
    "classification":     "MiniMax-M3.1-Flash-Preview",
    "summary":            "MiniMax-M3.1-Flash-Preview",
    "alert_classify":    "MiniMax-M3.1-Flash-Preview",
    # 长文本
    "rag_qa":             "qwen-long",
}

_CONFIG: Dict[str, str] = copy.deepcopy(DEFAULT_CONFIG)


def route_model(task_type: str) -> str:
    """根据 task_type 返回默认模型名；未知任务用 _CONFIG['default']，再不行用 'MiniMax-M3'"""
    if task_type in _CONFIG:
        return _CONFIG[task_type]
    if "default" in _CONFIG:
        return _CONFIG["default"]
    return "MiniMax-M3"


def set_config(custom_config: Dict[str, str]) -> None:
    """覆盖部分 / 全部路由"""
    global _CONFIG
    _CONFIG.update(custom_config)


def reset_config() -> None:
    """还原默认"""
    global _CONFIG
    _CONFIG = copy.deepcopy(DEFAULT_CONFIG)


def chat_for_task(
    task_type: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> Dict[str, Any]:
    """根据 task_type 路由到合适模型，再调 chat()"""
    model = route_model(task_type)
    return llm_client.chat(messages, model=model, temperature=temperature, max_tokens=max_tokens)
