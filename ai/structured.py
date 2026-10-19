"""
结构化输出协议与专业性辅助（专业性优化核心底座）。

提供：
- call_llm(task_type, messages, ...)  —— 统一走 ai.router.chat_for_task（模型路由）。
- compute_confidence(stock_code)      —— 由数据覆盖度 + 因子分歧度确定性推导 0-1 置信度。
- extract_score(text)                 —— 从 LLM 输出「## 评分与置信度」中抽取 0-100 评分。
- build_data_citations(stock_code)    —— 来源标注：数据类（finance_core / factors / 市值快照 / 行情）。
- attach_disclaimer(text)             —— 在最外层统一追加 spec §6.5 免责声明（仅一次）。
- degraded_result(...)                —— LLM 降级时的统一返回结构。
"""
from __future__ import annotations
import re
import time
from typing import Dict, Any, List, Optional

from etl.db import get_conn
from ai import router
from ai.client import estimate_cost
from ai.prompts import DISCLAIMER_SEC65


def call_llm(task_type: str, messages: List[Dict[str, str]],
             temperature: float = 0.3, max_tokens: int = 2048) -> Dict[str, Any]:
    """统一经模型路由调用 LLM（深度任务走 M3，轻量走 Flash）。"""
    return router.chat_for_task(task_type, messages, temperature=temperature, max_tokens=max_tokens)


def compute_confidence(stock_code: str) -> float:
    """置信度（0-1）：由数据覆盖度确定性推导，不额外调 LLM。

    覆盖度权重：finance_core 财务(0.3) + factors_daily 因子(0.2)
              + stock_market_cap 市值快照(0.2) + 研报/公告/笔记文档(0.3)。
    无数据时取最低 0.3，避免给出虚高置信度。
    """
    conn = get_conn()
    cov = 0.0
    try:
        if conn.execute("SELECT 1 FROM finance_core WHERE stock_code=? LIMIT 1", [stock_code]).fetchone():
            cov += 0.3
        if conn.execute("SELECT 1 FROM factors_daily WHERE stock_code=? LIMIT 1", [stock_code]).fetchone():
            cov += 0.2
        if conn.execute("SELECT 1 FROM stock_market_cap WHERE stock_code=? LIMIT 1", [stock_code]).fetchone():
            cov += 0.2
        if conn.execute(
            "SELECT 1 FROM doc WHERE stock_code=? AND doc_type IN ('report','research','announcement','note') LIMIT 1",
            [stock_code]).fetchone():
            cov += 0.3
    except Exception:
        pass
    return round(min(0.99, max(0.3, cov)), 2)


def extract_score(text: Optional[str]) -> Optional[int]:
    """从 LLM 输出的「## 评分与置信度」中抽取 0-100 综合评分。"""
    if not text:
        return None
    m = re.search(r"综合评分[（(]0-100[）)]\s*[:：]?\s*(\d{1,3})", text)
    if m:
        v = int(m.group(1))
        return v if 0 <= v <= 100 else None
    m = re.search(r"评分\s*[:：]\s*(\d{1,3})", text)
    if m:
        v = int(m.group(1))
        return v if 0 <= v <= 100 else None
    return None


def build_data_citations(stock_code: str) -> List[Dict[str, str]]:
    """数据类来源标注（type=data），仅列出确实含该代码的表。"""
    conn = get_conn()
    cites: List[Dict[str, str]] = []
    try:
        if conn.execute("SELECT 1 FROM finance_core WHERE stock_code=? LIMIT 1", [stock_code]).fetchone():
            cites.append({"type": "data", "ref": "finance_core",
                          "label": "财务核心指标（finance_core）", "date": ""})
        if conn.execute("SELECT 1 FROM factors_daily WHERE stock_code=? LIMIT 1", [stock_code]).fetchone():
            cites.append({"type": "data", "ref": "factors_daily",
                          "label": "Alpha 因子（factors_daily）", "date": ""})
        if conn.execute("SELECT 1 FROM stock_market_cap WHERE stock_code=? LIMIT 1", [stock_code]).fetchone():
            cites.append({"type": "data", "ref": "stock_market_cap",
                          "label": "全市场估值快照（stock_market_cap）", "date": ""})
        if conn.execute("SELECT 1 FROM daily_kline WHERE stock_code=? AND k_type=1 LIMIT 1", [stock_code]).fetchone():
            cites.append({"type": "data", "ref": "daily_kline",
                          "label": "交易所日行情数据（daily_kline）", "date": ""})
    except Exception:
        pass
    return cites


def attach_disclaimer(text: str) -> str:
    """在最外层统一追加 §6.5 免责声明（已存在则不重复）。"""
    if not text:
        return text or ""
    if DISCLAIMER_SEC65 in text:
        return text
    return text.rstrip() + f"\n\n---\n*{DISCLAIMER_SEC65}*"


def degraded_result(stock_code: str, atype: str, model: str = "",
                    duration_ms: int = 0) -> Dict[str, Any]:
    """LLM 降级时的统一结构化返回（不再冒泡 500）。"""
    return {
        "analysis_id": None,
        "stock_code": stock_code,
        "type": atype,
        "response": "（模型暂时不可用，已降级返回，请稍后重试）",
        "model": model,
        "tokens": 0,
        "cost_cny": 0.0,
        "duration_ms": duration_ms,
        "score": None,
        "confidence": 0.3,
        "citations": [],
        "disclaimer": True,
        "degraded": True,
    }


def run_simple(task_type: str, stock_code: str, prompt: str, save: bool,
               *, temperature: float = 0.3, max_tokens: int = 1600,
               atype: str = None, store_code: str = None,
               doc_citations: List[Dict[str, str]] = None,
               data_citations: List[Dict[str, str]] = None,
               confidence: float = None, apply_framework: bool = True) -> Dict[str, Any]:
    """单类型分析器统一收尾：路由调用 + 框架约束 + 评分/置信度/引用 + 降级兜底。

    各 analyzer 只需拼好 prompt，其余（调用、缓存、评分、落库、结构化返回）在此完成。
    """
    from ai.prompts import with_framework
    from ai.analyzers.base import save_analysis
    store_code = store_code or stock_code
    if apply_framework:
        prompt = with_framework(prompt)

    t0 = time.time()
    resp = call_llm(task_type, [{"role": "user", "content": prompt}],
                    temperature=temperature, max_tokens=max_tokens)
    dur = int((time.time() - t0) * 1000)

    if resp.get("degraded"):
        return degraded_result(store_code, atype or task_type, resp.get("model", ""), dur)

    cost = estimate_cost(resp["input_tokens"], resp["output_tokens"], resp["model"])
    conf = confidence if confidence is not None else compute_confidence(store_code)
    score = extract_score(resp["text"]) or round(conf * 100)
    data_cites = data_citations if data_citations is not None else build_data_citations(store_code)
    cites = data_cites + (doc_citations or [])
    labels = [c.get("label", "") for c in cites]

    aid = None
    if save:
        aid = save_analysis(store_code, atype or task_type, prompt, resp["text"],
                            resp["model"], resp["total_tokens"], cost, dur,
                            citations=labels, score=score, confidence=conf,
                            citations_obj=cites)
    return {
        "analysis_id": aid, "stock_code": store_code, "type": atype or task_type,
        "response": resp["text"], "model": resp["model"],
        "tokens": resp["total_tokens"], "cost_cny": cost, "duration_ms": dur,
        "score": score, "confidence": conf, "citations": cites,
        "disclaimer": True, "degraded": False,
    }
