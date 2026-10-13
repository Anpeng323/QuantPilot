"""宏观解读：基于 macro_indicator（CPI / PMI / LPR）最新趋势，分析宏观位置与板块影响。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from jinja2 import Template
from etl.db import get_conn
from ai import client as llm
from ai.prompts import PROMPT_MACRO
from ai.analyzers.base import save_analysis


def _series(indicator: str, n: int = 4):
    conn = get_conn()
    return conn.execute(
        "SELECT trade_date, value FROM macro_indicator WHERE indicator=? "
        "ORDER BY trade_date DESC LIMIT ?", [indicator, n]
    ).fetchall()


def build_context() -> str:
    ctx = "## 宏观指标（最新值 vs 上期）\n\n"
    labels = {
        "cpi": "CPI 同比（%）",
        "pmi": "制造业 PMI",
        "lpr_1y": "LPR 1年期（%）",
        "lpr_5y": "LPR 5年期（%）",
    }
    for ind, label in labels.items():
        rows = _series(ind, 3)
        if not rows:
            ctx += f"- {label}：无数据\n"
            continue
        latest = rows[0]
        prev = rows[1] if len(rows) > 1 else None
        arrow = ""
        if prev:
            diff = latest[1] - prev[1]
            arrow = " ▲ 上行" if diff > 0 else (" ▼ 下行" if diff < 0 else " → 持平")
        ctx += f"- {label}：最新 {latest[1]}（{latest[0]}）{arrow}"
        if prev:
            ctx += f"（上期 {prev[1]} @ {prev[0]}）"
        ctx += "\n"
        # 近 3 期明细
        ctx += "  - 近 3 期：" + " / ".join(f"{r[0]}={r[1]}" for r in rows) + "\n"
    ctx += "\n> 数据来源：akshare 宏观（CPI/PMI/LPR），盘后自动回填。\n"
    return ctx


def run(stock_code: str = "", save: bool = True) -> dict:
    ctx = build_context()
    tpl = Template(PROMPT_MACRO)
    prompt = tpl.render(macro_context=ctx)
    t0 = __import__("time").time()
    resp = llm.chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=1600)
    dur = int((__import__("time").time() - t0) * 1000)
    cost = llm.estimate_cost(resp["input_tokens"], resp["output_tokens"], resp["model"])
    aid = save_analysis("MACRO", "macro", prompt, resp["text"],
                        resp["model"], resp["total_tokens"], cost, dur) if save else None
    return {"analysis_id": aid, "stock_code": stock_code or "MACRO", "type": "macro",
            "response": resp["text"], "model": resp["model"],
            "tokens": resp["total_tokens"], "cost_cny": cost, "duration_ms": dur}
