"""财报解读"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
from jinja2 import Template
from etl.db import get_conn
from ai import client as llm
from ai.prompts import PROMPT_FINANCIAL
from ai.analyzers.base import stock_basic, save_analysis, _fmt_table


def build_context(stock_code: str) -> tuple:
    """从 DuckDB 拼出财报上下文"""
    info = stock_basic(stock_code)
    if not info:
        return info, "(无数据)"
    ctx = f"\n## 股票 {info['stock_code']} {info['short_name']} ({info['exchange']})\n"
    conn = get_conn()
    # 最近 30 天日 K（看趋势）
    daily = conn.execute("""
        SELECT trade_date, close, volume, amount
        FROM daily_kline WHERE stock_code=? AND k_type=1
        ORDER BY trade_date DESC LIMIT 30
    """, [stock_code]).fetchall()
    # 因子（动量/波动等）
    factors = conn.execute("""
        SELECT trade_date, mom_20d, mom_60d, vol_20d, bias_ma20, volume_ratio_20d
        FROM factors_daily WHERE stock_code=?
        ORDER BY trade_date DESC LIMIT 5
    """, [stock_code]).fetchall()

    ctx += f"## 上市日期：{info['list_date']}\n"
    ctx += "\n## 最近 30 个交易日\n"
    ctx += _fmt_table(
        list(reversed(daily)),
        ["日期", "收盘", "成交量", "成交额"],
        max_rows=30,
    )
    ctx += "\n\n## 最近 5 个交易日的 Alpha 因子\n"
    ctx += _fmt_table(
        list(reversed(factors)),
        ["日期", "20日动量", "60日动量", "20日波动率", "MA20偏离", "量比"],
        max_rows=5,
    )

    # 财务核心指标（finance_core，最新 8 期）
    fin = conn.execute("""
        SELECT report_date, eps, roe, gross_margin, revenue, net_profit,
               yoy_revenue, yoy_profit
        FROM finance_core WHERE stock_code=?
        ORDER BY report_date DESC LIMIT 8
    """, [stock_code]).fetchall()
    if fin:
        ctx += "\n\n## 财务核心指标（近 8 期，来自 finance_core）\n"
        ctx += _fmt_table(
            list(reversed(fin)),
            ["报告期", "EPS", "ROE%", "毛利率%", "营收(元)", "净利润(元)",
             "营收同比%", "净利同比%"],
            max_rows=8,
        )
        latest = fin[0]
        ctx += (f"\n> 最新报告期 {latest[0]}：EPS={latest[1]}, ROE={latest[2]}%, "
                f"毛利率={latest[3]}%, 营收={latest[4]:.0f}元, 净利润={latest[5]:.0f}元, "
                f"营收同比={latest[6]}%, 净利同比={latest[7]}%\n")

    # 风险科目（来自三表明细 finance_statement 最新一期）
    risk = conn.execute("""
        SELECT item_key, item_name, value FROM finance_statement
        WHERE stock_code=? AND statement_type IN ('balance','cashflow')
          AND item_key IN ('total_assets','total_liab','accounts_rece','inventory',
                           'goodwill','net_operate_cf')
          AND report_date = (SELECT MAX(report_date) FROM finance_statement
                             WHERE stock_code=? AND statement_type IN ('balance','cashflow'))
    """, [stock_code, stock_code]).fetchall()
    if risk:
        ctx += "\n## 关键风险科目（最新报告期，来自 finance_statement）\n"
        ctx += _fmt_table(risk, ["科目键", "科目", "数值(元)"], max_rows=10)
        # 简单勾稽：应收账款/存货/商誉 与总资产比
        d = {r[0]: r[2] for r in risk}
        ta = d.get("total_assets")
        if ta:
            for k in ("accounts_rece", "inventory", "goodwill", "net_operate_cf"):
                if d.get(k) is not None:
                    ctx += f"- {k} 占资产比 = {d[k]/ta*100:.1f}%\n"
    else:
        ctx += "\n\n> 注：finance_core / finance_statement 暂无该股票数据，财报解读缺少财务明细，"
        ctx += "仅基于行情与因子（建议先运行 data_ingest.finance_statements / finance_adata 接入）。\n"

    return info, ctx


def run(stock_code: str, save: bool = True) -> dict:
    info, ctx = build_context(stock_code)
    if not info:
        return {"error": f"找不到股票 {stock_code}"}
    tpl = Template(PROMPT_FINANCIAL)
    prompt = tpl.render(stock_code=info["stock_code"], short_name=info["short_name"],
                        financial_context=ctx)
    t0 = __import__("time").time()
    resp = llm.chat([{"role": "user", "content": prompt}],
                    temperature=0.3, max_tokens=2000)
    dur = int((__import__("time").time() - t0) * 1000)
    cost = llm.estimate_cost(resp["input_tokens"], resp["output_tokens"], resp["model"])
    aid = None
    if save:
        aid = save_analysis(stock_code, "financial_summary", prompt, resp["text"],
                            resp["model"], resp["total_tokens"], cost, dur)
    return {
        "analysis_id": aid,
        "stock_code": stock_code,
        "type": "financial_summary",
        "response": resp["text"],
        "model": resp["model"],
        "tokens": resp["total_tokens"],
        "cost_cny": cost,
        "duration_ms": dur,
    }
