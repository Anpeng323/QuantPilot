"""风险扫描"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from jinja2 import Template
from etl.db import get_conn
from ai import client as llm
from ai.prompts import PROMPT_RISK
from ai.analyzers.base import stock_basic, save_analysis


def build_context(stock_code: str) -> tuple:
    info = stock_basic(stock_code)
    if not info:
        return info, ""
    conn = get_conn()
    # 最近波动、量比、放量异动
    high_vol = conn.execute("""
        SELECT trade_date, close, ROUND(change_pct, 2) AS pct, volume
        FROM daily_kline WHERE stock_code=? AND k_type=1
        ORDER BY ABS(change_pct) DESC LIMIT 5
    """, [stock_code]).fetchall()
    # 最近 5 日换手率统计
    recent = conn.execute("""
        SELECT d.trade_date, d.close, d.volume, ROUND(change_pct, 2) AS pct,
               vol_20d, bias_ma20
        FROM factors_daily f JOIN daily_kline d
          ON f.stock_code=d.stock_code AND f.trade_date=d.trade_date
        WHERE f.stock_code=?
        ORDER BY f.trade_date DESC LIMIT 10
    """, [stock_code]).fetchall()

    ctx  = f"## 股票基础\n代码: {info['stock_code']} {info['short_name']} ({info['exchange']})\n\n"
    ctx += "## 近期最大波动日（按涨跌幅绝对值）\n"
    ctx += "| 日期 | 收盘 | 涨跌幅% | 成交量 |\n|---|---|---|---|\n"
    for r in high_vol:
        ctx += f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} |\n"
    ctx += "\n## 最近 10 个交易日的风险相关因子\n"
    ctx += "| 日期 | 收盘 | 涨跌幅% | 20日波动率 | MA20偏离 |\n|---|---|---|---|---|\n"
    for r in list(reversed(recent)):
        ctx += f"| {r[0]} | {r[1]} | {r[3]} | {r[4]} | {r[5]} |\n"
    ctx += "\n## 通用风险检查（v1）\n- 商誉/质押/减持等深度数据需接入公告库（v2 接入 cninfo 后完善）\n"
    return info, ctx


def run(stock_code: str, save: bool = True) -> dict:
    info, ctx = build_context(stock_code)
    if not info:
        return {"error": f"找不到股票 {stock_code}"}
    tpl = Template(PROMPT_RISK)
    prompt = tpl.render(stock_code=info["stock_code"], short_name=info["short_name"],
                        risk_context=ctx)
    t0 = __import__("time").time()
    resp = llm.chat([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=1500)
    dur = int((__import__("time").time() - t0) * 1000)
    cost = llm.estimate_cost(resp["input_tokens"], resp["output_tokens"], resp["model"])
    aid = save_analysis(stock_code, "risk_scan", prompt, resp["text"],
                        resp["model"], resp["total_tokens"], cost, dur) if save else None
    return {"analysis_id": aid, "stock_code": stock_code, "type": "risk_scan",
            "response": resp["text"], "model": resp["model"],
            "tokens": resp["total_tokens"], "cost_cny": cost, "duration_ms": dur}
