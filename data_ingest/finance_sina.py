"""
财务摘要接入（新浪源，免费，无需 token）：akshare stock_financial_abstract。

背景：原 finance_statement / finance_core 依赖东方财富（本机网络受限不可达）。
本模块用新浪财务摘要作为替代源，写入**独立新表 finance_abstract**，
**不改动**现有 finance_statement / finance_core（避免破坏因子计算链路）。

写入：
  finance_abstract(stock_code, report_date, category, indicator, value)
  category ∈ {常用指标, 每股指标, 盈利能力, 成长能力, 收益质量, 财务风险, 营运能力}
  value 为原始数值（元 / % / 比率等，依指标而定，未做单位换算）。

用法：
  python3 -m data_ingest.finance_sina            # 渐进回填（默认每夜 100 只）
  python3 -m data_ingest.finance_sina --limit 10 # 前 10 只验证
  python3 -m data_ingest.finance_sina --force     # 全量重抓
"""
from __future__ import annotations
import argparse
import math
import re
from etl.db import get_conn
import akshare as ak
import pandas as pd

MAX_PERIODS = 24  # 每只股票保留最近约 24 个报告期（约 6 年季度数据）

_DATE_RE = re.compile(r"^\d{8}$")


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


def _date_cols(df):
    cols = [c for c in df.columns if isinstance(c, str) and _DATE_RE.match(c)]
    cols.sort()  # 升序（最旧在前）
    return cols[-MAX_PERIODS:]  # 取最近 N 期


def fetch_abstract(code: str) -> list:
    """抓一只股票的新浪财务摘要，返回 [(stock_code, report_date, category, indicator, value), ...]。"""
    try:
        df = ak.stock_financial_abstract(symbol=code)
    except Exception:
        return []
    if df is None or len(df) == 0:
        return []
    dcols = _date_cols(df)
    if not dcols:
        return []
    rows = []
    for _, r in df.iterrows():
        cat = r.get("选项")
        ind = r.get("指标")
        if pd.isna(cat) or pd.isna(ind):
            continue
        cat, ind = str(cat).strip(), str(ind).strip()
        if not cat or not ind:
            continue
        for dc in dcols:
            v = _num(r.get(dc))
            if v is None:
                continue
            rd = f"{dc[:4]}-{dc[4:6]}-{dc[6:8]}"
            rows.append((code, rd, cat, ind, v))
    return rows


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS finance_abstract (
            stock_code  VARCHAR,
            report_date DATE,
            category    VARCHAR,
            indicator   VARCHAR,
            value       DOUBLE,
            PRIMARY KEY (stock_code, report_date, category, indicator)
        )
    """)


def _save(code, rows, conn):
    if not rows:
        return 0
    conn.execute("DELETE FROM finance_abstract WHERE stock_code=?", [code])
    conn.executemany(
        "INSERT OR REPLACE INTO finance_abstract VALUES (?,?,?,?,?)", rows
    )
    return len(rows)


def run_incremental(per_night: int = 100) -> dict:
    """渐进式回填：每夜只处理最多 per_night 只尚未入库的股票（同 finance_statements 思路）。"""
    conn = get_conn()
    _ensure_table(conn)
    codes = [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_basic ORDER BY stock_code"
    ).fetchall()]
    added = 0
    scanned = 0
    for code in codes:
        if conn.execute(
            "SELECT 1 FROM finance_abstract WHERE stock_code=?", [code]
        ).fetchone():
            continue
        scanned += 1
        rows = fetch_abstract(code)
        if rows:
            _save(code, rows, conn)
            added += 1
        if added >= per_night:
            break
    print(f"新浪财务摘要增量：本夜新增 {added} 只（已扫 {scanned} 只待补）")
    return {"added": added, "scanned": scanned}


def run(limit: int = None, force: bool = False) -> dict:
    conn = get_conn()
    _ensure_table(conn)
    codes = [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_basic ORDER BY stock_code"
    ).fetchall()]
    if limit:
        codes = codes[:limit]
    added = 0
    skipped = 0
    for i, code in enumerate(codes, 1):
        if not force and conn.execute(
            "SELECT 1 FROM finance_abstract WHERE stock_code=?", [code]
        ).fetchone():
            skipped += 1
            continue
        rows = fetch_abstract(code)
        if rows:
            _save(code, rows, conn)
            added += 1
        if i % 100 == 0:
            print(f"  ...{i}/{len(codes)} 已处理（跳过{skipped}）")
    print(f"新浪财务摘要接入完成：新增 {added} 只，跳过 {skipped} 只")
    return {"stocks": len(codes), "added": added, "skipped": skipped}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--per-night", type=int, default=100)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.limit:
        run(limit=a.limit, force=a.force)
    else:
        run_incremental(per_night=a.per_night)
