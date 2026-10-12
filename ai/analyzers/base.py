"""
Analyzer 基类：定义统一接口 + 上下文拼接。
"""
from __future__ import annotations
import time
import uuid
import json
from typing import Dict, Any, List, Optional
from jinja2 import Template
from etl.db import get_conn


def _fmt_table(rows, headers, max_rows=20):
    """格式化为 markdown 表格"""
    if not rows:
        return "（无数据）"
    rows = rows[:max_rows]
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(v) for v in r) + " |")
    return "\n".join(lines)


def stock_basic(stock_code: str) -> dict:
    """取一只股票的基础信息"""
    conn = get_conn()
    row = conn.execute(
        "SELECT stock_code, short_name, exchange, list_date FROM stock_basic WHERE stock_code=?",
        [stock_code]
    ).fetchone()
    if not row:
        return {}
    return {"stock_code": row[0], "short_name": row[1],
            "exchange": row[2], "list_date": str(row[3]) if row[3] else ""}


def save_analysis(stock_code: str, analysis_type: str, prompt: str,
                  response: str, model: str, tokens_used: int,
                  cost_cny: float, duration_ms: int,
                  citations: Optional[List[str]] = None) -> str:
    aid = uuid.uuid4().hex[:16]
    conn = get_conn()
    conn.execute("""
        INSERT INTO ai_analysis
        (analysis_id, stock_code, analysis_type, prompt, response, citations,
         model_used, tokens_used, cost_cny, duration_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [aid, stock_code, analysis_type, prompt[:10000], response[:8000],
          json.dumps(citations or []), model, tokens_used, cost_cny, duration_ms])
    return aid
