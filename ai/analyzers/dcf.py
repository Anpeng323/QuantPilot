"""DCF 简化估值：基于 finance_core 净利润（自由现金流代理）+ 当前股价，测算内在价值。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from jinja2 import Template
from etl.db import get_conn
from ai.prompts import PROMPT_DCF
from ai.analyzers.base import stock_basic
from ai import router
from ai.structured import run_simple


def _dcf(fcf0: float, shares: float, g1: float = 0.08, g_term: float = 0.03, r: float = 0.09, n: int = 5):
    """返回 (intrinsic_total_value, intrinsic_per_share)。"""
    pv = 0.0
    fcf = fcf0
    for t in range(1, n + 1):
        fcf *= (1 + g1)
        pv += fcf / ((1 + r) ** t)
    tv = fcf * (1 + g_term) / (r - g_term)   # 第 n 年末终值
    pv += tv / ((1 + r) ** n)
    per_share = pv / shares if shares else 0.0
    return pv, per_share


def build_context(stock_code: str) -> tuple:
    info = stock_basic(stock_code)
    if not info:
        return info, ""
    conn = get_conn()
    price = conn.execute(
        "SELECT close FROM daily_kline WHERE stock_code=? AND k_type=1 "
        "ORDER BY trade_date DESC LIMIT 1", [stock_code]
    ).fetchone()
    fin = conn.execute("""
        SELECT net_profit, roe, total_market_cap, report_date
        FROM finance_core WHERE stock_code=?
        ORDER BY report_date DESC LIMIT 1
    """, [stock_code]).fetchone()

    # 总市值优先用市值快照（每日刷新、覆盖面全），回退 finance_core
    mcap_snap = conn.execute(
        "SELECT total_market_cap FROM stock_market_cap WHERE stock_code=?", [stock_code]
    ).fetchone()
    mcap = (mcap_snap[0] if mcap_snap and mcap_snap[0] is not None else None) \
        or (fin[2] if fin else None)

    ctx = f"## 股票\n代码: {info['stock_code']} {info['short_name']}\n\n"
    if not price:
        ctx += "> 注：无最新行情价，无法测算。\n"
        return info, ctx
    price = price[0]
    ctx += f"## 当前股价: {price:.2f} 元\n\n"

    if not fin or fin[0] is None:
        ctx += "> 注：finance_core 无该股票净利润数据，无法做 DCF（建议先接入核心财务）。\n"
        return info, ctx

    net_profit, roe, _mcap_fc, rdate = fin
    shares = (mcap / price) if mcap else None
    # 经营现金流（优先作 FCF 代理；缺失则退回净利润）
    cf = conn.execute(
        "SELECT value FROM finance_statement WHERE stock_code=? AND statement_type='cashflow' "
        "AND item_key='net_operate_cf' ORDER BY report_date DESC LIMIT 1", [stock_code]
    ).fetchone()
    fcf0 = cf[0] if cf else net_profit
    proxy_name = "经营现金流净额(net_operate_cf)" if cf else "净利润(代理)"
    ctx += f"## 财务数据（报告期 {rdate}，来自 finance_core + cashflow）\n"
    ctx += f"- 净利润: {net_profit:,.0f} 元\n"
    if cf:
        ctx += f"- 经营现金流净额(最新): {cf[0]:,.0f} 元\n"
    ctx += f"- FCF 代理采用: {proxy_name} = {fcf0:,.0f} 元\n"
    ctx += f"- ROE: {roe}%\n" if roe is not None else ""
    ctx += f"- 总市值: {mcap/1e8:.1f} 亿元\n" if mcap else ""
    ctx += f"- 估算总股本（市值/股价）: {shares:,.0f} 股\n" if shares else ""

    # 敏感性：增速 5%/8%/11% × 折现率 8%/9%/10%
    ctx += "\n## DCF 测算（FCF 代理；高增 5 年 g1，永续 g=3%）\n"
    ctx += "| 增速\\折现 | 8% | 9% | 10% |\n|---|---|---|---|\n"
    for g1 in (0.05, 0.08, 0.11):
        row = []
        for r in (0.08, 0.09, 0.10):
            if shares:
                _, ps = _dcf(fcf0, shares, g1=g1, r=r)
                row.append(f"{ps:.2f}")
            else:
                row.append("-")
        ctx += f"| {g1*100:.0f}% | " + " | ".join(row) + " |\n"
    # 基准情景（企业整体内在价值始终可算；每股价值需有总股本估算）
    pv, ps = _dcf(fcf0, shares or 1, g1=0.08, r=0.09)
    ctx += f"\n## 基准情景（g1=8%, r=9%）\n"
    ctx += f"- 企业内在价值（折现值）: {pv:,.0f} 元\n"
    if shares:
        margin = (ps - price) / price * 100
        ctx += f"- 每股内在价值: {ps:.2f} 元\n"
        ctx += f"- 当前股价: {price:.2f} 元\n"
        ctx += f"- 安全边际: {margin:+.1f}%（{'低估' if margin>0 else '高估'}）\n"
    else:
        ctx += "- 每股内在价值：无法计算（finance_core 无总市值，无法估算总股本）。\n"
    if cf:
        ctx += "\n> 说明：已采用经营现金流净额作 FCF 代理；若经营现金流与净利润长期背离（如重资产/金融企业），结论需谨慎。\n"
    else:
        ctx += "\n> 局限：未取到经营现金流，以净利润作 FCF 代理会高估/低估，仅作方向性参考。\n"
    return info, ctx


def run(stock_code: str, save: bool = True) -> dict:
    info, ctx = build_context(stock_code)
    if not info:
        return {"error": f"找不到股票 {stock_code}"}
    tpl = Template(PROMPT_DCF)
    prompt = tpl.render(dcf_context=ctx)
    return run_simple(router.TASK_DCF, stock_code, prompt, save,
                      temperature=0.2, max_tokens=1600, atype="dcf")
