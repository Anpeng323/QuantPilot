"""多公司对比分析：横向对比若干标的的财务与估值指标。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from jinja2 import Template
from etl.db import get_conn
from ai.prompts import PROMPT_COMPARATIVE
from ai.analyzers.base import stock_basic, _fmt_table
from ai import router
from ai.structured import run_simple, compute_confidence


def _latest_price(code: str):
    conn = get_conn()
    return conn.execute(
        "SELECT close FROM daily_kline WHERE stock_code=? AND k_type=1 "
        "ORDER BY trade_date DESC LIMIT 1", [code]
    ).fetchone()


def build_context(stock_codes: list) -> tuple:
    conn = get_conn()
    rows = []
    for code in stock_codes:
        info = stock_basic(code)
        if not info:
            continue
        fin = conn.execute("""
            SELECT eps, roe, gross_margin, yoy_revenue, yoy_profit, pe, pb, total_market_cap
            FROM finance_core WHERE stock_code=?
            ORDER BY report_date DESC LIMIT 1
        """, [code]).fetchone()
        price = _latest_price(code)
        # finance_core 覆盖有限 → PE/PB/市值回退到全市场估值快照
        snap = conn.execute(
            "SELECT price, pe, pb, total_market_cap FROM stock_market_cap WHERE stock_code=?",
            [code]).fetchone()
        pe = fin[5] if fin else None
        pb = fin[6] if fin else None
        mcap = fin[7] if fin else None
        if snap:
            pe = pe if pe is not None else snap[1]
            pb = pb if pb is not None else snap[2]
            mcap = mcap if mcap is not None else snap[3]
        rows.append({
            "code": code,
            "name": info["short_name"],
            "price": price[0] if price else (snap[0] if snap else None),
            "eps": fin[0] if fin else None,
            "roe": fin[1] if fin else None,
            "gm": fin[2] if fin else None,
            "yoy_rev": fin[3] if fin else None,
            "yoy_pft": fin[4] if fin else None,
            "pe": pe,
            "pb": pb,
            "mcap": mcap,
        })
    if not rows:
        return rows, "（无有效股票数据）"
    table = _fmt_table(
        [(r["code"], r["name"],
          f'{r["price"]:.2f}' if r["price"] else "-",
          f'{r["pe"]:.1f}' if r["pe"] else "-",
          f'{r["pb"]:.2f}' if r["pb"] else "-",
          f'{r["mcap"]/1e8:.1f}' if r["mcap"] else "-",
          f'{r["eps"]}' if r["eps"] else "-",
          f'{r["roe"]:.2f}' if r["roe"] is not None else "-",
          f'{r["gm"]:.2f}' if r["gm"] is not None else "-",
          f'{r["yoy_rev"]:.2f}' if r["yoy_rev"] is not None else "-",
          f'{r["yoy_pft"]:.2f}' if r["yoy_pft"] is not None else "-")
         for r in rows],
        ["代码", "名称", "最新价", "PE", "PB", "市值(亿)", "EPS", "ROE%", "毛利率%", "营收同比%", "净利同比%"],
    )
    ctx = "## 对比标的清单\n" + ", ".join(r["code"] for r in rows) + "\n\n"
    ctx += "## 财务指标对比表\n" + table + "\n\n"
    ctx += "> 注：部分字段可能为 -。PE/PB/市值优先取 finance_core，缺失时回退全市场估值快照 stock_market_cap。\n"
    return rows, ctx


def run(stock_code: str = "", save: bool = True, stock_codes: list = None) -> dict:
    codes = list(stock_codes or [])
    if stock_code and stock_code not in codes:
        codes.insert(0, stock_code)
    codes = [c for c in codes if c]
    if len(codes) < 2:
        return {"error": "对比分析至少需要 2 只股票（stock_codes 列表）"}
    rows, ctx = build_context(codes)
    if not rows:
        return {"error": "未找到有效股票数据"}
    tpl = Template(PROMPT_COMPARATIVE)
    prompt = tpl.render(compare_context=ctx)
    store_code = ",".join(codes)
    conf = min((compute_confidence(c) for c in codes), default=0.3)
    return run_simple(router.TASK_COMPARATIVE, store_code, prompt, save,
                      temperature=0.3, max_tokens=1800, atype="comparative",
                      confidence=conf)
