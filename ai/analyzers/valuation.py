"""估值测算"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from jinja2 import Template
from etl.db import get_conn
from ai import client as llm
from ai.prompts import PROMPT_VALUATION
from ai.analyzers.base import stock_basic, save_analysis


def build_context(stock_code: str) -> tuple:
    info = stock_basic(stock_code)
    if not info:
        return info, ""
    conn = get_conn()
    prices = conn.execute("""
        SELECT trade_date, close, ROUND(change_pct, 2) AS pct
        FROM daily_kline WHERE stock_code=? AND k_type=1
        ORDER BY trade_date DESC LIMIT 30
    """, [stock_code]).fetchall()
    # 近 1 年价格区间（粗略百分位）
    stats = conn.execute("""
        SELECT MIN(close), MAX(close), AVG(close),
               COUNT(*) FILTER (WHERE trade_date > CURRENT_DATE - INTERVAL '30 days') AS days30
        FROM daily_kline WHERE stock_code=? AND k_type=1
          AND trade_date >= CURRENT_DATE - INTERVAL '365 days'
    """, [stock_code]).fetchone()

    ctx  = f"## 股票\n代码: {info['stock_code']} {info['short_name']}\n\n"
    if stats and stats[0]:
        ctx += f"## 近 1 年价格统计\n- 最低: {stats[0]:.2f}\n- 最高: {stats[1]:.2f}\n- 平均: {stats[2]:.2f}\n- 30日交易天数: {stats[3]}\n\n"
    ctx += "## 最近 30 个交易日收盘价\n"
    for r in reversed(prices[-30:]):
        ctx += f"- {r[0]}  收盘 {r[1]}  ({(r[2] or 0):+.2f}%)\n"
    ctx += "\n## 注\n- 完整估值需 PE/PB/EPS/ROE 等数据，需接入财报库\n- v1 仅提供基于历史价格区间的简化模板\n"
    return info, ctx


def run(stock_code: str, save: bool = True) -> dict:
    info, ctx = build_context(stock_code)
    if not info:
        return {"error": f"找不到股票 {stock_code}"}
    tpl = Template(PROMPT_VALUATION)
    prompt = tpl.render(valuation_context=ctx)
    t0 = __import__("time").time()
    resp = llm.chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=1500)
    dur = int((__import__("time").time() - t0) * 1000)
    cost = llm.estimate_cost(resp["input_tokens"], resp["output_tokens"], resp["model"])
    aid = save_analysis(stock_code, "valuation", prompt, resp["text"],
                        resp["model"], resp["total_tokens"], cost, dur) if save else None
    return {"analysis_id": aid, "stock_code": stock_code, "type": "valuation",
            "response": resp["text"], "model": resp["model"],
            "tokens": resp["total_tokens"], "cost_cny": cost, "duration_ms": dur}
