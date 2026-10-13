"""盈利拆解 / 利润归因：结合 finance_core（当期、含同比）与 finance_statement 利润表明细（费用结构），
拆解收入→毛利→营业利润→归母净利的传导与费用拖累。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from jinja2 import Template
from etl.db import get_conn
from ai import client as llm
from ai.prompts import PROMPT_PROFIT
from ai.analyzers.base import stock_basic, save_analysis

# 数据新鲜度基准：报告期年份落后此值视为陈旧（当前数据环境约 2026 年）
REF_YEAR = 2026


def _profit_sheet(code: str, report_date) -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT item_key, value FROM finance_statement "
        "WHERE stock_code=? AND statement_type='profit' AND report_date=?",
        [code, report_date],
    ).fetchall()
    return {k: v for k, v in rows}


def _ratio(num, den):
    try:
        if num is None or den in (None, 0):
            return None
        return num / den
    except Exception:
        return None


def build_context(stock_code: str) -> tuple:
    info = stock_basic(stock_code)
    if not info:
        return info, ""
    conn = get_conn()
    ctx = f"## 股票\n代码: {info['stock_code']} {info['short_name']}\n\n"

    # 当期核心指标（finance_core，通常较新）
    fc = conn.execute("""
        SELECT report_date, revenue, net_profit, gross_margin, yoy_revenue, yoy_profit, roe, eps
        FROM finance_core WHERE stock_code=?
        ORDER BY report_date DESC LIMIT 1
    """, [stock_code]).fetchone()

    # 利润表明细（可能陈旧）
    pdates = [r[0] for r in conn.execute(
        "SELECT DISTINCT report_date FROM finance_statement "
        "WHERE stock_code=? AND statement_type='profit' ORDER BY report_date DESC",
        [stock_code],
    ).fetchall()]
    cur = _profit_sheet(stock_code, pdates[0]) if pdates else {}
    prev = _profit_sheet(stock_code, pdates[1]) if len(pdates) > 1 else {}

    # 数据新鲜度
    stale = bool(pdates) and int(str(pdates[0])[:4]) < REF_YEAR - 1
    ctx += "## 数据说明\n"
    if fc:
        ctx += f"- finance_core 当期报告期: {fc[0]}（较新，优先采用）\n"
    if pdates:
        flag = "⚠️ 陈旧" if stale else "较新"
        ctx += f"- 利润表明细最新报告期: {pdates[0]}（{flag}）\n"
    else:
        ctx += "- 利润表明细：无数据（费用结构无法拆解）\n"

    # 当期规模（优先 finance_core，回退利润表同周期）
    rev = (fc[1] if fc else None) or cur.get("revenue")
    np_attr = (fc[2] if fc else None) or cur.get("net_profit_attr")
    yoy_rev = (fc[4] if fc is not None else None) or cur.get("revenue_yoy")
    yoy_pft = (fc[5] if fc is not None else None) or cur.get("net_profit_attr_yoy")
    gm_fc = fc[3] if fc else None
    # 利润表同周期收入（与明细口径一致，可能陈旧）
    rev_stmt = cur.get("revenue")

    ctx += "\n## 收入与利润规模（finance_core 当期）\n"
    ctx += f"- 营业收入: {rev:,.0f} 元\n" if rev else "- 营业收入: 无\n"
    ctx += f"- 归母净利润: {np_attr:,.0f} 元\n" if np_attr else "- 归母净利润: 无\n"
    ctx += f"- 营收同比(YoY): {yoy_rev:.2f}%\n" if yoy_rev is not None else "- 营收同比: 无\n"
    ctx += f"- 净利同比(YoY): {yoy_pft:.2f}%\n" if yoy_pft is not None else "- 净利同比: 无\n"

    # 利润传导（利润表同周期口径：分母统一用利润表自身收入，避免跨期错配）
    gp = cur.get("gross_profit")
    op = cur.get("operate_profit")
    tp = cur.get("total_profit")
    np_stmt = cur.get("net_profit_attr") or cur.get("net_profit")
    gm_stmt = _ratio(gp, rev_stmt)
    opm = _ratio(op, rev_stmt)
    nm_stmt = _ratio(np_stmt, rev_stmt)
    ctx += "\n## 利润传导（基于利润表同周期口径）\n"
    if gm_fc is not None:
        ctx += f"- 毛利率（finance_core 当期）: {gm_fc:.2f}%\n"
    if gm_stmt is not None:
        ctx += f"- 毛利率（利润表测算）: {gm_stmt*100:.2f}%\n"
    ctx += f"- 营业利润率: {opm*100:.2f}%\n" if opm is not None else "- 营业利润率（明细缺营业利润）: 无\n"
    ctx += f"- 净利率（利润表口径）: {nm_stmt*100:.2f}%\n" if nm_stmt is not None else "- 净利率: 无\n"

    # 费用拆解（同周期口径）
    sell = cur.get("selling_expense")
    admin = cur.get("admin_expense")
    fin = cur.get("finance_expense")
    rd = cur.get("rd_expense")
    exp_total = (sell or 0) + (admin or 0) + (fin or 0) + (rd or 0)
    exp_ratio = _ratio(exp_total, rev_stmt)
    ctx += "\n## 费用拆解（占营收比，利润表口径）\n"
    if rev_stmt:
        ctx += f"- 销售费用: {sell:,.0f}（{_ratio(sell,rev_stmt)*100:.2f}%）\n" if sell is not None else "- 销售费用: 无\n"
        ctx += f"- 管理费用: {admin:,.0f}（{_ratio(admin,rev_stmt)*100:.2f}%）\n" if admin is not None else "- 管理费用: 无\n"
        ctx += f"- 研发费用: {rd:,.0f}（{_ratio(rd,rev_stmt)*100:.2f}%）\n" if rd is not None else "- 研发费用: 无\n"
        ctx += f"- 财务费用: {fin:,.0f}（{_ratio(fin,rev_stmt)*100:.2f}%）\n" if fin is not None else "- 财务费用: 无\n"
        ctx += f"- 四项费用合计占营收: {exp_ratio*100:.2f}%\n" if exp_ratio is not None else ""
    else:
        ctx += "- 因缺利润表收入基数，费用率无法计算。\n"

    # 同比费用率变化（同周期口径）
    if prev and rev_stmt:
        prev_rev = prev.get("revenue")
        prev_exp = ((prev.get("selling_expense") or 0) + (prev.get("admin_expense") or 0)
                   + (prev.get("finance_expense") or 0) + (prev.get("rd_expense") or 0))
        prev_ratio = _ratio(prev_exp, prev_rev)
        if exp_ratio is not None and prev_ratio is not None:
            ctx += f"\n## 费用率同比变化\n- 本期 {exp_ratio*100:.2f}% vs 上期 {prev_ratio*100:.2f}%（Δ={(exp_ratio-prev_ratio)*100:+.2f}pp，报告期 {pdates[1]}）\n"

    if stale:
        ctx += "\n> ⚠️ 利润表明细报告期较早，费用结构与利润率可能为历史值，请结合最新财报判断。\n"
    return info, ctx


def run(stock_code: str, save: bool = True) -> dict:
    info, ctx = build_context(stock_code)
    if not info:
        return {"error": f"找不到股票 {stock_code}"}
    tpl = Template(PROMPT_PROFIT)
    prompt = tpl.render(profit_context=ctx)
    t0 = __import__("time").time()
    resp = llm.chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=1600)
    dur = int((__import__("time").time() - t0) * 1000)
    cost = llm.estimate_cost(resp["input_tokens"], resp["output_tokens"], resp["model"])
    aid = save_analysis(stock_code, "profit", prompt, resp["text"],
                        resp["model"], resp["total_tokens"], cost, dur) if save else None
    return {"analysis_id": aid, "stock_code": stock_code, "type": "profit",
            "response": resp["text"], "model": resp["model"],
            "tokens": resp["total_tokens"], "cost_cny": cost, "duration_ms": dur}
