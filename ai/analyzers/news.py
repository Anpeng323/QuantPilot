"""新闻 AI 分析：情绪 / 摘要 / 影响 / 要点 / 风险 / 关注点。

对单条新闻（doc.doc_type='news'）调用 LLM，产出结构化 JSON：
  {sentiment, confidence, summary, key_points[], impact, risk, watch}
结果以 doc_id 为键落库 news_analysis（幂等 upsert），可缓存复用；
detail 列以 JSON 承载 confidence/key_points/risk/watch/version。
同时提供 run_for_stock(code) 批量分析某股全部新闻并汇总情绪分布。
"""
from __future__ import annotations
import json
import re
import time
import uuid
from typing import Dict, Any, List, Optional

from jinja2 import Template
from etl.db import get_conn
from ai import client as llm
from ai.prompts import PROMPT_NEWS

# 分析结构版本：升级提示词/字段时递增，用于让旧缓存自动失效重算
ANALYSIS_VERSION = 2


# 防御性建表（web 启动不调 init_schema，这里确保表存在）
_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS news_analysis (
    doc_id       VARCHAR PRIMARY KEY,
    stock_code   VARCHAR(6),
    sentiment    VARCHAR(8),
    summary      VARCHAR(512),
    impact_text  VARCHAR(1024),
    detail       TEXT,
    model_used   VARCHAR(32),
    tokens_used  INT,
    cost_cny     DOUBLE,
    created_at   TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS idx_news_analysis_stock ON news_analysis(stock_code);
"""


def _ensure_table():
    conn = get_conn()
    conn.execute(_CREATE_SQL)
    # 兼容旧表：补充 detail 列（已存在则忽略）
    try:
        conn.execute("ALTER TABLE news_analysis ADD COLUMN detail TEXT")
    except Exception:
        pass


def _parse_json(text: str) -> Dict[str, Any]:
    """从 LLM 文本中稳健解析 JSON。失败则降级为纯文本摘要 + 中性。"""
    txt = (text or "").strip()
    # 去掉 ```json ... ``` 包裹
    m = re.search(r"\{.*\}", txt, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and "sentiment" in obj:
                return obj
        except Exception:
            pass
    return {
        "sentiment": "中性",
        "confidence": "低",
        "summary": txt[:150] if txt else "（分析失败，无可用内容）",
        "key_points": [],
        "impact": "影响有限",
        "risk": "",
        "watch": "",
    }


def _normalize(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """规范化 LLM 输出字段，容错缺失/类型错误。"""
    sentiment = parsed.get("sentiment", "中性")
    if sentiment not in ("利好", "利空", "中性"):
        sentiment = "中性"
    confidence = parsed.get("confidence", "中")
    if confidence not in ("高", "中", "低"):
        confidence = "中"
    kp = parsed.get("key_points") or []
    if isinstance(kp, str):
        kp = [kp]
    kp = [str(x).strip() for x in kp if str(x).strip()][:6]
    return {
        "sentiment": sentiment,
        "confidence": confidence,
        "summary": (parsed.get("summary") or "").strip(),
        "key_points": kp,
        "impact": (parsed.get("impact") or "影响有限").strip(),
        "risk": (parsed.get("risk") or "").strip(),
        "watch": (parsed.get("watch") or "").strip(),
    }


def run(doc_id: str, save: bool = True, force: bool = False) -> Dict[str, Any]:
    """分析单条新闻（带缓存）。

    force=False 且缓存为当前版本时直接复用；否则调用 LLM 重算并落库。
    返回 {doc_id, stock_code, sentiment, confidence, summary, key_points,
          impact, risk, watch, version, cached, model, tokens, cost_cny, duration_ms}。
    """
    _ensure_table()
    conn = get_conn()
    row = conn.execute(
        "SELECT doc_id, title, source, stock_code, metadata FROM doc WHERE doc_id=?",
        [doc_id],
    ).fetchone()
    if not row:
        return {"error": f"找不到新闻 {doc_id}"}

    # 命中当前版本缓存则直接复用
    if not force:
        cached = get_analysis(doc_id)
        if cached and cached.get("version") == ANALYSIS_VERSION:
            cached["cached"] = True
            return cached

    meta = {}
    if row[4]:
        try:
            meta = json.loads(row[4])
        except Exception:
            meta = {}
    title = row[1] or ""
    source = row[2] or ""
    stock_code = row[3] or ""
    summary = meta.get("summary", "") or title

    tpl = Template(PROMPT_NEWS)
    prompt = tpl.render(title=title, source=source, summary=summary, stock_code=stock_code)
    t0 = time.time()
    resp = llm.chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=1500)
    dur = int((time.time() - t0) * 1000)
    cost = llm.estimate_cost(resp["input_tokens"], resp["output_tokens"], resp["model"])

    norm = _normalize(_parse_json(resp["text"]))
    detail = {
        "confidence": norm["confidence"],
        "key_points": norm["key_points"],
        "risk": norm["risk"],
        "watch": norm["watch"],
        "version": ANALYSIS_VERSION,
    }

    if save:
        _save(doc_id, stock_code, norm["sentiment"], norm["summary"], norm["impact"],
              detail, resp["model"], resp["total_tokens"], cost)

    return {
        "doc_id": doc_id,
        "stock_code": stock_code,
        "sentiment": norm["sentiment"],
        "confidence": norm["confidence"],
        "summary": norm["summary"],
        "key_points": norm["key_points"],
        "impact": norm["impact"],
        "risk": norm["risk"],
        "watch": norm["watch"],
        "version": ANALYSIS_VERSION,
        "cached": False,
        "model": resp["model"],
        "tokens": resp["total_tokens"],
        "cost_cny": cost,
        "duration_ms": dur,
    }


def _save(doc_id, stock_code, sentiment, summary, impact, detail, model, tokens, cost):
    _ensure_table()
    conn = get_conn()
    detail_json = json.dumps(detail or {}, ensure_ascii=False)
    exists = conn.execute("SELECT 1 FROM news_analysis WHERE doc_id=?", [doc_id]).fetchone()
    if exists:
        conn.execute("""
            UPDATE news_analysis
            SET stock_code=?, sentiment=?, summary=?, impact_text=?, detail=?,
                model_used=?, tokens_used=?, cost_cny=?, created_at=now()
            WHERE doc_id=?
        """, [stock_code or "", sentiment, summary[:512], impact[:1024], detail_json,
              model, tokens, cost, doc_id])
    else:
        conn.execute("""
            INSERT INTO news_analysis
            (doc_id, stock_code, sentiment, summary, impact_text, detail, model_used, tokens_used, cost_cny)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [doc_id, stock_code or "", sentiment, summary[:512], impact[:1024], detail_json,
              model, tokens, cost])


def get_analysis(doc_id: str) -> Optional[Dict[str, Any]]:
    """读取已缓存的新闻分析（含 detail 中的要点/风险/关注点/版本）。"""
    _ensure_table()  # 兼容旧表：确保 detail 列存在
    conn = get_conn()
    row = conn.execute(
        "SELECT doc_id, stock_code, sentiment, summary, impact_text, detail, "
        "model_used, tokens_used, cost_cny, created_at "
        "FROM news_analysis WHERE doc_id=?",
        [doc_id],
    ).fetchone()
    if not row:
        return None
    detail: Dict[str, Any] = {}
    if row[5]:
        try:
            detail = json.loads(row[5])
        except Exception:
            detail = {}
    return {
        "doc_id": row[0], "stock_code": row[1] or "", "sentiment": row[2],
        "summary": row[3], "impact": row[4],
        "confidence": detail.get("confidence", ""),
        "key_points": detail.get("key_points", []),
        "risk": detail.get("risk", ""),
        "watch": detail.get("watch", ""),
        "version": detail.get("version", 1),
        "model": row[6], "tokens": row[7], "cost_cny": row[8], "created_at": str(row[9]),
    }


def run_for_stock(stock_code: str) -> Dict[str, Any]:
    """批量分析某股票的全部新闻，并汇总情绪分布。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT doc_id FROM doc WHERE doc_type='news' AND stock_code=? ORDER BY publish_date DESC",
        [stock_code],
    ).fetchall()
    results: List[Dict] = []
    dist = {"利好": 0, "利空": 0, "中性": 0}
    for (doc_id,) in rows:
        r = run(doc_id, save=True)
        if "error" in r:
            continue
        results.append(r)
        dist[r["sentiment"]] = dist.get(r["sentiment"], 0) + 1
    return {
        "stock_code": stock_code,
        "count": len(results),
        "distribution": dist,
        "items": results,
    }
