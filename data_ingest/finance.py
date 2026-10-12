"""
财报数据接入（v1 简化版）
- 当前使用 Tushare（如已配 token）→ 否则 mock 返回空
- 数据写入 DuckDB finance_core 表
"""
import uuid
import time
import json
from datetime import datetime
from etl.db import get_conn

try:
    import tushare as ts
    TUSHARE_TOKEN = ""
except ImportError:
    TUSHARE_TOKEN = ""


def fetch_finance(stock_code: str, year: int = None, quarter: int = None,
                  use_tushare: bool = False) -> list:
    """
    取一只股票最近几个季度的财务数据。
    v1: use_tushare=False 时返回空（避免没装 tushare 报错）。
    """
    if not use_tushare:
        return []
    if not TUSHARE_TOKEN:
        return []
    try:
        pro = ts.pro_api(TUSHARE_TOKEN)
        df = pro.fina_indicator(ts_code=_to_tscode(stock_code), limit=10)
        return df.to_dict("records")
    except Exception as e:
        return []


def _to_tscode(stock_code: str) -> str:
    """6 位代码 → ts_code（如 000001.SZ）"""
    if stock_code.startswith("6"):
        return f"{stock_code}.SH"
    return f"{stock_code}.SZ"


def save_finance(rows: list, stock_code: str) -> int:
    """把 tushare rows 写入 finance_core"""
    n = 0
    conn = get_conn()
    for r in rows:
        try:
            conn.execute("""
                INSERT INTO finance_core
                (stock_code, report_date, eps, roe, gross_margin, revenue,
                 net_profit, yoy_revenue, yoy_profit, pe, pb, ps, total_market_cap)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [stock_code,
                  r.get("end_date"),
                  r.get("eps"),
                  r.get("roe"),
                  r.get("grossprofit_margin"),
                  r.get("revenue"),
                  r.get("net_profit"),
                  r.get("yoy_revenue"),
                  r.get("yoy_profit"),
                  r.get("pe"),
                  r.get("pb"),
                  r.get("ps"),
                  r.get("total_market_cap")])
            n += 1
        except Exception:
            pass
    return n
