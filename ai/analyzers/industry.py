"""行业景气度分析：聚合个股所在申万一级行业的财务/行情/公告，输出景气判断。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from jinja2 import Template
from etl.db import get_conn
from ai import client as llm
from ai.prompts import PROMPT_INDUSTRY
from ai.analyzers.base import stock_basic, save_analysis, _fmt_table


def _industry_name(stock_code: str) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT industry_name FROM stock_industry "
        "WHERE stock_code=? AND industry_type='申万一级' LIMIT 1",
        [stock_code],
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT industry_name FROM stock_industry WHERE stock_code=? LIMIT 1",
            [stock_code],
        ).fetchone()
    return row[0] if row else ""


def build_context(stock_code: str) -> tuple:
    info = stock_basic(stock_code)
    if not info:
        return info, ""
    conn = get_conn()
    industry = _industry_name(stock_code)
    ctx = f"## 股票\n代码: {info['stock_code']} {info['short_name']}\n"
    ctx += f"## 所属申万一级行业: {industry or '（无行业数据）'}\n\n"

    if not industry:
        ctx += "> 注：stock_industry 无该股票行业归属，无法聚合行业数据。\n"
        return info, ctx

    members = [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_industry WHERE industry_name=?", [industry]
    ).fetchall()]
    ctx += f"## 行业成分股数量: {len(members)}\n\n"

    # 行业财务聚合（finance_core 最新一期）
    fin = conn.execute(f"""
        SELECT f.stock_code, f.roe, f.gross_margin, f.yoy_revenue, f.yoy_profit, f.pe
        FROM finance_core f
        WHERE f.stock_code IN ({','.join('?'*len(members))})
          AND f.report_date = (
            SELECT MAX(report_date) FROM finance_core WHERE stock_code=f.stock_code)
    """, members).fetchall() if members else []
    if fin:
        roes = [r[1] for r in fin if r[1] is not None]
        gms = [r[2] for r in fin if r[2] is not None]
        yrev = [r[3] for r in fin if r[3] is not None]
        yprf = [r[4] for r in fin if r[4] is not None]
        pes = [r[5] for r in fin if r[5] is not None]
        def avg(x):
            return sum(x)/len(x) if x else None
        ctx += "## 行业财务聚合（finance_core 最新一期，有数据公司）\n"
        ctx += f"- 公司数: {len(fin)}\n"
        ctx += f"- 平均 ROE: {avg(roes):.2f}%\n" if avg(roes) is not None else "- 平均 ROE: 无\n"
        ctx += f"- 平均毛利率: {avg(gms):.2f}%\n" if avg(gms) is not None else "- 平均毛利率: 无\n"
        ctx += f"- 平均营收同比: {avg(yrev):.2f}%\n" if avg(yrev) is not None else "- 平均营收同比: 无\n"
        ctx += f"- 平均净利同比: {avg(yprf):.2f}%\n" if avg(yprf) is not None else "- 平均净利同比: 无\n"
        ctx += f"- 平均 PE: {avg(pes):.1f}\n" if avg(pes) is not None else "- 平均 PE: 无\n"
        ctx += "\n"
    else:
        ctx += "> 注：finance_core 暂未覆盖该行业公司，财务聚合缺失（盘后回填将逐步补齐）。\n\n"

    # 行业近期行情表现（最新交易日涨跌）
    if members:
        ph = ",".join("?"*len(members))
        rows = conn.execute(f"""
            SELECT d.stock_code, d.change_pct FROM daily_kline d
            WHERE d.stock_code IN ({ph}) AND d.k_type=1
              AND d.trade_date = (SELECT MAX(trade_date) FROM daily_kline WHERE stock_code=d.stock_code AND k_type=1)
        """, members).fetchall()
        up = sum(1 for r in rows if r[1] is not None and r[1] > 0)
        down = sum(1 for r in rows if r[1] is not None and r[1] < 0)
        ctx += f"## 行业最新交易日表现: 上涨 {up} / 下跌 {down} / 共 {len(rows)} 只\n\n"

    # 行业近期公告（风险/催化线索）
    ann = []
    if members:
        ph2 = ",".join("?"*len(members))
        ann = conn.execute(f"""
            SELECT stock_code, title, publish_date FROM doc
            WHERE doc_type='announcement' AND stock_code IN ({ph2})
            ORDER BY publish_date DESC LIMIT 15
        """, members).fetchall()
    if ann:
        ctx += "## 行业近期公告（前 15 条）\n"
        ctx += _fmt_table(
            [(a[0], a[2], a[1][:40]) for a in ann],
            ["代码", "日期", "标题(截断)"],
        )
        ctx += "\n"
    else:
        ctx += "> 注：公告库暂未覆盖该行业公司。\n"

    return info, ctx


def run(stock_code: str, save: bool = True) -> dict:
    info, ctx = build_context(stock_code)
    if not info:
        return {"error": f"找不到股票 {stock_code}"}
    industry = _industry_name(stock_code)
    tpl = Template(PROMPT_INDUSTRY)
    prompt = tpl.render(stock_code=info["stock_code"], short_name=info["short_name"],
                        industry_name=industry, industry_context=ctx)
    t0 = __import__("time").time()
    resp = llm.chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=1600)
    dur = int((__import__("time").time() - t0) * 1000)
    cost = llm.estimate_cost(resp["input_tokens"], resp["output_tokens"], resp["model"])
    aid = save_analysis(stock_code, "industry", prompt, resp["text"],
                        resp["model"], resp["total_tokens"], cost, dur) if save else None
    return {"analysis_id": aid, "stock_code": stock_code, "type": "industry",
            "response": resp["text"], "model": resp["model"],
            "tokens": resp["total_tokens"], "cost_cny": cost, "duration_ms": dur}
