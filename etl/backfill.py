"""
首次全量回填（backfill）。
- 股票代码来自 adata 内置 CSV（5641 只，几秒搞定）
- K 线走新浪接口（稳定可达），datalen=1500 即最近约 6 年
- 交易日历走 adata
- 指数走新浪
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import adata
from etl.db import get_conn
from etl.utils import get_logger, throttle, upsert_df, job_runner
from etl.fetchers import fetch_sina_kline, fetch_sina_finance_index

log = get_logger("backfill")
ADATA_CODE_CSV = Path(adata.__file__).resolve().parent / "stock" / "cache" / "code.csv"


@job_runner("backfill.basic", table_name="stock_basic")
def backfill_stock_basic(conn, force_refresh: bool = False) -> int:
    if force_refresh:
        log.info("拉最新全量股票列表（联网） ...")
        df = adata.stock.info.all_code()
    else:
        log.info(f"从 adata 本地 CSV 读取股票列表: {ADATA_CODE_CSV}")
        df = pd.read_csv(ADATA_CODE_CSV, dtype={"stock_code": str})
        df["stock_code"] = df["stock_code"].str.zfill(6)
        df["list_date"]  = pd.to_datetime(df.get("list_date2"), errors="coerce")
        df["exchange"] = df["stock_code"].apply(
            lambda c: "SH" if c.startswith("6")
            else "SZ" if c.startswith(("0", "3"))
            else "BJ" if c.startswith(("4", "8"))
            else None
        )
        df = df[["stock_code", "short_name", "exchange", "list_date"]]

    df["stock_code"] = df["stock_code"].astype(str).str.zfill(6)
    df["short_name"] = df["short_name"].astype(str).str.replace(" ", "")
    added, updated = upsert_df(conn, df, "stock_basic", ["stock_code"], log)
    log.info(f"股票基础信息完成，共 {len(df)} 只")
    return added


@job_runner("backfill.calendar", table_name="trade_calendar")
def backfill_trade_calendar(conn, years: list[int]) -> int:
    log.info(f"开始拉取交易日历：{years}")
    total = 0
    for y in years:
        df = adata.stock.info.trade_calendar(year=y)
        if df is None or df.empty:
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df["source"] = "szse"
        upsert_df(conn, df, "trade_calendar", ["trade_date"], log)
        total += len(df)
    return total


@throttle(seconds=0.05)
def fetch_one_stock(stock_code: str, k_type: int, datalen: int) -> pd.DataFrame:
    return fetch_sina_kline(stock_code, k_type=k_type, datalen=datalen)


@job_runner("backfill.daily_kline", table_name="daily_kline")
def backfill_daily_kline(conn, k_type: int = 1, datalen: int = 1500,
                         limit: int | None = None,
                         only_codes: list[str] | None = None,
                         batch_size: int = 200) -> int:
    codes = [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_basic ORDER BY stock_code"
    ).fetchall()]
    if only_codes:
        codes = only_codes
    if limit:
        codes = codes[:limit]

    log.info(f"准备拉 {len(codes)} 只票的 K 线（k_type={k_type}, datalen={datalen}）")
    total = 0
    failed = []
    batch_dfs: list[pd.DataFrame] = []

    for i, code in enumerate(codes, 1):
        try:
            df = fetch_one_stock(code, k_type, datalen)
            if df.empty:
                continue
            batch_dfs.append(df)
            total += len(df)
        except Exception as e:
            failed.append((code, str(e)[:80]))
            log.warning(f"  {code} 失败: {e}")

        if len(batch_dfs) >= batch_size:
            upsert_df(conn, pd.concat(batch_dfs, ignore_index=True), "daily_kline",
                      ["stock_code", "trade_date", "k_type", "adjust_type"], log=None)
            batch_dfs.clear()

        if i % 200 == 0 or i == len(codes):
            log.info(f"  进度: {i}/{len(codes)} ({i*100//len(codes)}%)  累计 {total:,} 条")

    if batch_dfs:
        upsert_df(conn, pd.concat(batch_dfs, ignore_index=True), "daily_kline",
                  ["stock_code", "trade_date", "k_type", "adjust_type"], log=None)

    if failed:
        log.warning(f"失败的股票数: {len(failed)}，前几只：{failed[:5]}")
    return total


@job_runner("backfill.index_kline", table_name="index_kline")
def backfill_index_kline(conn) -> int:
    INDEX_CODES = {
        "sh000001": "上证指数",
        "sz399001": "深证成指",
        "sz399006": "创业板指",
        "sh000300": "沪深300",
        "sh000905": "中证500",
        "sh000688": "科创50",
    }
    total = 0
    for code, name in INDEX_CODES.items():
        try:
            df = fetch_sina_finance_index(code, datalen=1500)
            if df.empty:
                continue
            upsert_df(conn, df, "index_kline", ["index_code", "trade_date"], log)
            total += len(df)
            log.info(f"  指数 {code} ({name}): {len(df)} 条")
        except Exception as e:
            log.warning(f"  指数 {code} 失败: {e}")
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--codes", nargs="*", default=None)
    parser.add_argument("--skip", default="", help="basic,calendar,kline,index")
    parser.add_argument("--refresh-basic", action="store_true")
    parser.add_argument("--k-type", type=int, default=1)
    parser.add_argument("--datalen", type=int, default=1500)
    args = parser.parse_args()

    skip = set(args.skip.split(",")) if args.skip else set()
    if "basic" not in skip:
        backfill_stock_basic(force_refresh=args.refresh_basic)
    if "calendar" not in skip:
        backfill_trade_calendar(years=[2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026])
    if "kline" not in skip:
        backfill_daily_kline(k_type=args.k_type, datalen=args.datalen,
                             limit=args.limit or None, only_codes=args.codes)
    if "index" not in skip:
        backfill_index_kline()

    with get_conn(read_only=True) as conn:
        stats = conn.execute("""
            SELECT
              (SELECT COUNT(*) FROM stock_basic)    AS stocks,
              (SELECT COUNT(*) FROM daily_kline WHERE k_type=1) AS daily_k,
              (SELECT COUNT(*) FROM trade_calendar) AS calendar,
              (SELECT COUNT(*) FROM index_kline)    AS indices
        """).fetchone()
    log.info(f"\n========= 回填完成 =========")
    log.info(f"  股票数: {stats[0]:,}")
    log.info(f"  日K线条数: {stats[1]:,}")
    log.info(f"  交易日历: {stats[2]:,}")
    log.info(f"  指数K线: {stats[3]:,}")
