"""
宏观指标接入（akshare 免费，无需 token）。
写入 macro_indicator(indicator, trade_date, value, unit)。

指标：
- cpi   : 全国 CPI 当月值（%）
- pmi   : 制造业 PMI 指数
- lpr_1y: 贷款市场报价利率 1 年期（%）
- lpr_5y: 贷款市场报价利率 5 年期（%）

用法：
  python3 -m data_ingest.macro
"""
import re
from etl.db import get_conn
import akshare as ak


def _num(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.replace(",", "").strip()
        if v in ("", "-", "--", "None"):
            return None
    try:
        import math
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _month_date(s: str):
    """'2026年08月份' -> '2026-08-01'"""
    m = re.search(r"(\d{4})\D+(\d{1,2})", str(s))
    if not m:
        return None
    y, mo = m.group(1), int(m.group(2))
    return f"{y}-{mo:02d}-01"


def _store(indicator: str, df, date_col, val_col, unit: str):
    rows = []
    for _, r in df.iterrows():
        d = r[date_col]
        if isinstance(d, str) and ("年" in d or "月" in d):
            d = _month_date(d)
        else:
            d = str(d)[:10]
        v = _num(r[val_col])
        if d and v is not None:
            rows.append((indicator, d, v, unit))
    if rows:
        conn = get_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO macro_indicator VALUES (?,?,?,?)", rows
        )
    return len(rows)


def run() -> dict:
    res = {}
    # CPI
    try:
        df = ak.macro_china_cpi()
        res["cpi"] = _store("cpi", df, "月份", "全国-当月", "%")
    except Exception as e:
        print("cpi 获取失败:", e)
    # PMI
    try:
        df = ak.macro_china_pmi()
        res["pmi"] = _store("pmi", df, "月份", "制造业-指数", "")
    except Exception as e:
        print("pmi 获取失败:", e)
    # LPR
    try:
        df = ak.macro_china_lpr()
        n1 = _store("lpr_1y", df, "TRADE_DATE", "LPR1Y", "%")
        n5 = _store("lpr_5y", df, "TRADE_DATE", "LPR5Y", "%")
        res["lpr"] = n1 + n5
    except Exception as e:
        print("lpr 获取失败:", e)
    print("宏观接入完成:", res)
    return res


if __name__ == "__main__":
    run()
