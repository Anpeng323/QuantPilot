"""
财务三表明细接入（akshare 东方财富口径，免费，无需 token）。
写入 finance_statement（长表 EAV），仅抽取投研常用科目，兼容银行/制造业差异。

设计：
- 表 finance_statement(stock_code, statement_type, report_date, item_key, item_name, value)
- statement_type: balance / profit / cashflow
- item_key: 规范化英文键（营收/净利/ROE/应收/存货/商誉/经营现金流等约 25 项）

用法：
  python3 -m data_ingest.finance_statements --limit 10   # 前 10 只验证
  python3 -m data_ingest.finance_statements --force       # 全量重抓
  python3 -m data_ingest.finance_statements               # 增量（跳过已有）
"""
import argparse
import math
from etl.db import get_conn
import akshare as ak

MAX_PERIODS = 20  # 每只股票保留最近约 20 个报告期（约 5 年）

# 规范化科目映射：item_key, 中文名, akshare 候选列名（按顺序匹配第一个存在的）
FIN_MAP = {
    "profit": [
        ("revenue",           "营业收入",        ["OPERATE_INCOME"]),
        ("revenue_yoy",       "营收同比%",       ["OPERATE_INCOME_YOY"]),
        ("operate_cost",      "营业成本",        ["OPERATE_COST"]),
        ("gross_profit",      "毛利",           ["GROSS_PROFIT", "OPERATE_GROSS_PROFIT"]),
        ("operate_profit",    "营业利润",        ["OPERATE_PROFIT"]),
        ("total_profit",      "利润总额",        ["TOTAL_PROFIT"]),
        ("net_profit",        "净利润",          ["NETPROFIT", "NET_PROFIT", "TOTAL_NETPROFIT"]),
        ("net_profit_attr",   "归母净利润",      ["PARENT_NETPROFIT", "NETPROFIT_ATTR_SH"]),
        ("net_profit_attr_yoy","净利同比%",      ["PARENT_NETPROFIT_YOY", "NETPROFIT_YOY"]),
        ("eps",               "基本每股收益",    ["BASIC_EPS"]),
        ("roe",               "加权ROE%",        ["WEIGHTAVG_ROE", "ROE_WTD"]),
        ("selling_expense",   "销售费用",        ["SALE_EXPENSE", "SELLING_EXPENSE"]),
        ("admin_expense",     "管理费用",        ["MANAGE_EXPENSE"]),
        ("finance_expense",   "财务费用",        ["FINANCE_EXPENSE"]),
        ("rd_expense",        "研发费用",        ["RESEARCH_EXPENSE", "RD_EXPENSE"]),
    ],
    "balance": [
        ("total_assets",      "资产总计",        ["TOTAL_ASSETS"]),
        ("total_liab",        "负债合计",        ["TOTAL_LIAB"]),
        ("total_equity",      "所有者权益合计",  ["TOTAL_EQUITY", "TOTAL_OWNERS_EQUITY"]),
        ("monetary_cap",      "货币资金",        ["MONETARY_CAP", "CURRENCY_FUNDS"]),
        ("accounts_rece",     "应收账款",        ["ACCOUNTS_RECE", "ACCOUNTS_RECEIVABLE"]),
        ("notes_rece",        "应收票据",        ["NOTES_RECE", "NOTES_RECEIVABLE"]),
        ("inventory",         "存货",            ["INVENTORY", "STOCKS"]),
        ("fixed_assets",      "固定资产",        ["FIXED_ASSETS"]),
        ("goodwill",          "商誉",            ["GOODWILL"]),
        ("short_term_loan",   "短期借款",        ["SHORT_TERM_LOAN", "SHORT_TERM_BORROW"]),
        ("contract_liab",     "合同负债",        ["CONTRACT_LIAB", "CONTRACT_LIABILITY"]),
        ("other_rece",        "其他应收款",      ["OTHER_RECE", "OTHER_RECEIVABLE"]),
        ("prepayment",        "预付款项",        ["PREPAYMENT", "PREPAID_EXPENSE"]),
    ],
    "cashflow": [
        ("net_operate_cf",    "经营活动现金流净额", ["NETCASH_OPERATE", "OPERATE_NET_CASH_FLOW", "NET_OPERATE_CASH_FLOW"]),
        ("net_invest_cf",     "投资活动现金流净额", ["NETCASH_INVEST", "INVEST_NET_CASH_FLOW", "NET_INVEST_CASH_FLOW"]),
        ("net_finance_cf",    "筹资活动现金流净额", ["NETCASH_FINANCE", "FINANCE_NET_CASH_FLOW", "NET_FINANCE_CASH_FLOW"]),
        ("cash_equiv_increase","现金净增加额",     ["CASH_EQUIVALENT_INCREASE", "NETCASH_END"]),
        ("sale_cash",         "销售收现",          ["TOTAL_OPERATE_INFLOW", "SALE_SERVICES_CASH", "CASH_FROM_SALES"]),
    ],
}

FUNC = {
    "profit":   "stock_profit_sheet_by_report_em",
    "balance":  "stock_balance_sheet_by_report_em",
    "cashflow": "stock_cash_flow_sheet_by_report_em",
}


def _date(v):
    if v is None:
        return None
    s = str(v)[:10]
    try:
        from datetime import date
        return date.fromisoformat(s).isoformat()
    except Exception:
        return None


def _num(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.replace(",", "").strip()
        if v in ("", "-", "--", "None"):
            return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _val(row, candidates):
    for c in candidates:
        if c in row and row[c] is not None:
            n = _num(row[c])
            if n is not None:
                return n
    return None


def fetch_stock(code: str, exchange: str) -> int:
    """抓一只股票的三表，写入 finance_statement。返回写入条数。"""
    conn = get_conn()
    sym = f"{exchange}{code}"
    rows = []
    for stype, fn in FUNC.items():
        try:
            df = getattr(ak, fn)(symbol=sym)
        except Exception as e:
            print(f"  ! {code} {stype} 获取失败: {e}")
            continue
        if df is None or len(df) == 0:
            continue
        # akshare 返回通常按报告期降序（最新在前）；先升序排序再取最近 N 期，
        # 避免误取最旧的历史数据。同时按报告期去重（财报重述会出现重复期）。
        df = df.copy()
        df["REPORT_DATE"] = df["REPORT_DATE"].astype(str).str[:10]
        df = (df.sort_values("REPORT_DATE", ascending=True)
                .drop_duplicates("REPORT_DATE")
                .tail(MAX_PERIODS))
        for rec in df.to_dict("records"):
            rd = _date(rec.get("REPORT_DATE"))
            if not rd:
                continue
            for key, name, cands in FIN_MAP[stype]:
                v = _val(rec, cands)
                if v is None:
                    continue
                rows.append((code, stype, rd, key, name, v))
    if rows:
        conn.execute("DELETE FROM finance_statement WHERE stock_code=?", [code])
        conn.executemany(
            "INSERT OR REPLACE INTO finance_statement VALUES (?,?,?,?,?,?)", rows
        )
    return len(rows)


def run_incremental(per_night: int = 50) -> dict:
    """渐进式全市场回填：每夜只处理最多 per_night 只尚未入库的股票，避免单夜超时。
    配合 nightly 调度，约数十个夜晚即可铺满全市场。"""
    conn = get_conn()
    codes = [r for r in conn.execute(
        "SELECT stock_code, exchange FROM stock_basic ORDER BY stock_code"
    ).fetchall()]
    total = 0
    ok = 0
    scanned = 0
    for code, ex in codes:
        if conn.execute(
            "SELECT 1 FROM finance_statement WHERE stock_code=?", [code]
        ).fetchone():
            continue
        scanned += 1
        n = fetch_stock(code, ex or "SH")
        if n:
            total += n
            ok += 1
        if ok >= per_night:
            break
    print(f"三表明细增量接入：本夜新增 {ok} 只，共 {total} 条明细（已扫 {scanned} 只待补）")
    return {"added": ok, "rows": total, "scanned": scanned}


def run(limit: int = None, force: bool = False) -> dict:
    conn = get_conn()
    codes = [r for r in conn.execute(
        "SELECT stock_code, exchange FROM stock_basic ORDER BY stock_code"
    ).fetchall()]
    if limit:
        codes = codes[:limit]
    total = 0
    ok = 0
    skipped = 0
    for i, (code, ex) in enumerate(codes, 1):
        if not force and conn.execute(
            "SELECT 1 FROM finance_statement WHERE stock_code=?", [code]
        ).fetchone():
            skipped += 1
            continue
        n = fetch_stock(code, ex or "SH")
        if n:
            total += n
            ok += 1
        if i % 50 == 0:
            print(f"  ...{i}/{len(codes)} 已处理（跳过{skipped}）写入 {total} 条明细")
    print(f"三表明细接入完成：新增 {ok} 只，跳过 {skipped} 只，共 {total} 条明细")
    return {"stocks": len(codes), "added": ok, "skipped": skipped, "rows": total}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    run(limit=a.limit, force=a.force)
