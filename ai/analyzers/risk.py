"""风险扫描"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from jinja2 import Template
from etl.db import get_conn
from ai.prompts import PROMPT_RISK
from ai.analyzers.base import stock_basic
from ai import router
from ai.structured import run_simple


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
    # 近期公告风险关键词扫描（doc 表 doc_type='announcement'）
    KEYWORDS = ["减持", "质押", "冻结", "商誉", "诉讼", "立案", "警示", "问询",
                "预减", "亏损", "退市", "处罚", "计提", "减值", "终止", "违约",
                "担保", "查封", "立案调查", "监管"]
    ann = conn.execute(
        "SELECT title, publish_date, url FROM doc WHERE doc_type='announcement' AND stock_code=? "
        "ORDER BY publish_date DESC LIMIT 30", [stock_code]
    ).fetchall()
    hits = []
    for title, pdate, url in ann:
        for kw in KEYWORDS:
            if kw in (title or ""):
                hits.append((title, pdate, kw))
                break
    ctx += "\n## 近期公告风险线索（关键词扫描，来自公告库）\n"
    if hits:
        ctx += "| 日期 | 命中关键词 | 公告标题 |\n|---|---|---|\n"
        for title, pdate, kw in hits[:15]:
            ctx += f"| {pdate} | {kw} | {title[:40]} |\n"
        ctx += f"\n> 共命中 {len(hits)} 条风险相关公告（近 30 条内），建议结合原文研判。\n"
    else:
        ctx += "- 近 30 条公告中未命中预设风险关键词。\n"
        if not ann:
            ctx += "- 注：公告库暂无该股票公告，建议先运行 `data_ingest.announcement` 接入。\n"

    ctx += "\n## 通用风险检查\n- 财务科目类风险（商誉/应收/存货占资产比）见 finance_statement（若已接入三表明细）。\n"
    return info, ctx


def run(stock_code: str, save: bool = True) -> dict:
    info, ctx = build_context(stock_code)
    if not info:
        return {"error": f"找不到股票 {stock_code}"}
    tpl = Template(PROMPT_RISK)
    prompt = tpl.render(stock_code=info["stock_code"], short_name=info["short_name"],
                        risk_context=ctx)
    return run_simple(router.TASK_RISK, stock_code, prompt, save,
                      temperature=0.2, max_tokens=1500, atype="risk_scan")
