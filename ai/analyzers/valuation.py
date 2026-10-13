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
    # 当前价 + 财务（动态计算 PE/PB/EPS/ROE/市值）
    cur = conn.execute("""
        SELECT close FROM daily_kline WHERE stock_code=? AND k_type=1
        ORDER BY trade_date DESC LIMIT 1
    """, [stock_code]).fetchone()
    fin = conn.execute("""
        SELECT eps, roe, gross_margin, pe, pb, total_market_cap, report_date
        FROM finance_core WHERE stock_code=?
        ORDER BY report_date DESC LIMIT 1
    """, [stock_code]).fetchone()
    # finance_core 仅覆盖部分个股（PE/PB 常缺）→ 回退到全市场估值快照 stock_market_cap
    snap = conn.execute("""
        SELECT price, pe, pb, total_market_cap FROM stock_market_cap WHERE stock_code=?
    """, [stock_code]).fetchone()

    ctx  = f"## 股票\n代码: {info['stock_code']} {info['short_name']}\n\n"
    if cur:
        price = cur[0]
        ctx += f"## 当前价（最新收盘）: {price:.2f} 元\n"
        # 以快照补齐 finance_core 缺失的 PE/PB/市值
        pe_db = fin[3] if fin else None
        pb_db = fin[4] if fin else None
        mcap = fin[5] if fin else None
        if snap:
            pe_db = pe_db if pe_db is not None else snap[1]
            pb_db = pb_db if pb_db is not None else snap[2]
            mcap = mcap if mcap is not None else snap[3]
        if fin:
            eps, roe, gm, _pe, _pb, _m, rdate = fin
            ctx += f"## 财务数据（报告期 {rdate}，来自 finance_core）\n"
            ctx += f"- EPS: {eps}\n- ROE: {roe}%\n- 毛利率: {gm}%\n"
            if eps:
                ctx += f"- 动态 PE（现价/EPS）: {price/eps:.1f}\n"
        else:
            ctx += "\n> 注：finance_core 暂无该股票财务数据（核心财务覆盖有限），"
            ctx += "以下 PE/PB/市值取自全市场估值快照 stock_market_cap。\n"
        if mcap:
            ctx += f"- 总市值: {mcap/1e8:.1f} 亿元\n"
        if pe_db:
            ctx += f"- PE（{"库内" if fin and fin[3] is not None else "快照"}）: {pe_db}\n"
        if pb_db:
            ctx += f"- PB（{"库内" if fin and fin[4] is not None else "快照"}）: {pb_db}\n"
        if pe_db is None and pb_db is None:
            ctx += "（暂无 PE/PB 数据）\n"
    if stats and stats[0]:
        ctx += f"\n## 近 1 年价格统计\n- 最低: {stats[0]:.2f}\n- 最高: {stats[1]:.2f}\n- 平均: {stats[2]:.2f}\n- 30日交易天数: {stats[3]}\n"
    ctx += "\n## 最近 30 个交易日收盘价\n"
    for r in reversed(prices[-30:]):
        ctx += f"- {r[0]}  收盘 {r[1]}  ({(r[2] or 0):+.2f}%)\n"
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
