"""
每日增量更新（每日定时跑）
性能优化：单连接 + 批量事务
包含：股票基础、交易日历、日 K、指数 K、5 分钟 K 线
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import adata
from datetime import datetime
from etl.db import get_conn
from etl.utils import get_logger, job_runner, upsert_df
from etl.fetchers import fetch_sina_kline, fetch_sina_finance_index, fetch_sina_index_kline

log = get_logger("daily_update")


# ---------- 1. 股票基础信息 ----------
@job_runner("daily.basic", table_name="stock_basic")
def update_stock_basic(conn) -> int:
    csv_path = Path(adata.__file__).resolve().parent / "stock" / "cache" / "code.csv"
    df = pd.read_csv(csv_path, dtype={"stock_code": str})
    df["stock_code"] = df["stock_code"].str.zfill(6)
    df["list_date"]  = pd.to_datetime(df.get("list_date2"), errors="coerce")
    df["exchange"]   = df["stock_code"].apply(
        lambda c: "SH" if c.startswith("6")
        else "SZ" if c.startswith(("0", "3"))
        else "BJ" if c.startswith(("4", "8"))
        else None
    )
    df["short_name"] = df["short_name"].astype(str).str.replace(" ", "")
    df = df[["stock_code", "short_name", "exchange", "list_date"]]
    added, updated = upsert_df(conn, df, "stock_basic", ["stock_code"], log)
    return added


# ---------- 2. 交易日历 ----------
@job_runner("daily.calendar", table_name="trade_calendar")
def update_trade_calendar(conn) -> int:
    year = datetime.now().year
    df = adata.stock.info.trade_calendar(year=year)
    if df is None or df.empty:
        return 0
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["source"] = "szse"
    added, updated = upsert_df(conn, df, "trade_calendar", ["trade_date"], log)
    return added


# ---------- 3. 日 K 线 ----------
@job_runner("daily.daily_kline", table_name="daily_kline")
def update_daily_kline(conn, datalen: int = 30, batch_size: int = 300) -> int:
    codes = [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_basic ORDER BY stock_code"
    ).fetchall()]
    log.info(f"准备增量更新 {len(codes)} 只票的最近 {datalen} 天日 K")

    total = 0
    failed = []
    batch_dfs: list[pd.DataFrame] = []

    for i, code in enumerate(codes, 1):
        try:
            df = fetch_sina_kline(code, k_type=1, datalen=datalen)
            if df.empty:
                continue
            batch_dfs.append(df)
            total += len(df)
        except Exception as e:
            failed.append((code, str(e)[:80]))

        if len(batch_dfs) >= batch_size:
            upsert_df(conn, pd.concat(batch_dfs, ignore_index=True), "daily_kline",
                      ["stock_code", "trade_date", "k_type", "adjust_type"], log=None)
            batch_dfs.clear()

        if i % 500 == 0 or i == len(codes):
            log.info(f"  进度: {i}/{len(codes)} ({i*100//len(codes)}%)  本次 +{total:,} 条")

    if batch_dfs:
        upsert_df(conn, pd.concat(batch_dfs, ignore_index=True), "daily_kline",
                  ["stock_code", "trade_date", "k_type", "adjust_type"], log=None)

    if failed:
        log.warning(f"失败 {len(failed)} 只，前 5: {failed[:5]}")
    return total


# ---------- 4. 指数增量 ----------
@job_runner("daily.index_kline", table_name="index_kline")
def update_index_kline(conn) -> int:
    INDEX_CODES = ["sh000001", "sz399001", "sz399006", "sh000300", "sh000905", "sh000688"]
    total = 0
    for code in INDEX_CODES:
        try:
            df = fetch_sina_finance_index(code, datalen=30)
            if df.empty:
                continue
            upsert_df(conn, df, "index_kline", ["index_code", "trade_date"], log)
            total += len(df)
        except Exception as e:
            log.warning(f"指数 {code} 失败: {e}")
    return total


# ---------- 5. 5 分钟 K 线（每日增量） ----------
@job_runner("daily.minute_kline", table_name="minute_kline")
def update_minute_kline(conn, k_type: int = 5, datalen: int = 100, batch_size: int = 300) -> int:
    """
    每日增量：拉每只票最近 100 根 5min K（约 2 天），upsert 覆盖。
    5640 只 ≈ 56 万行，约 8-10 分钟。
    """
    codes = [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_basic ORDER BY stock_code"
    ).fetchall()]
    log.info(f"准备增量更新 {len(codes)} 只票的最近 {datalen} 根 {k_type}min K")

    batch_dfs = []
    total = 0
    failed = []

    for i, code in enumerate(codes, 1):
        try:
            df = fetch_sina_kline(code, k_type=k_type, datalen=datalen)
            if df.empty:
                continue
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
            df["trade_time"] = pd.to_datetime(df["trade_time"])
            df["k_type"]     = k_type
            df = df[["stock_code", "trade_time", "trade_date", "k_type",
                     "open", "high", "low", "close", "volume", "amount"]]
            batch_dfs.append(df)
            total += len(df)
        except Exception as e:
            failed.append((code, str(e)[:80]))

        if len(batch_dfs) >= batch_size:
            upsert_df(conn, pd.concat(batch_dfs, ignore_index=True), "minute_kline",
                      ["stock_code", "trade_time", "k_type"], log=None)
            batch_dfs.clear()

        if i % 1000 == 0 or i == len(codes):
            log.info(f"  进度: {i}/{len(codes)} ({i*100//len(codes)}%)  本次 +{total:,} 条")

    if batch_dfs:
        upsert_df(conn, pd.concat(batch_dfs, ignore_index=True), "minute_kline",
                  ["stock_code", "trade_time", "k_type"], log=None)
    if failed:
        log.warning(f"失败 {len(failed)} 只，前 5: {failed[:5]}")
    return total


# ---------- 6. 指数 5 分钟 K 线 ----------
@job_runner("daily.index_minute_kline", table_name="index_minute_kline")
def update_index_minute_kline(conn, k_type: int = 5, datalen: int = 100, batch_size: int = 50) -> int:
    """
    每日增量：拉每个指数最近 100 根 5min K（约 2 天），upsert 覆盖。
    """
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT index_code FROM index_kline ORDER BY index_code"
    ).fetchall()]
    log.info(f"准备增量更新 {len(codes)} 个指数的最近 {datalen} 根 {k_type}min K")

    batch_dfs = []
    total = 0
    failed = []

    for i, code in enumerate(codes, 1):
        try:
            df = fetch_sina_index_kline(code, k_type=k_type, datalen=datalen)
            if df.empty:
                continue
            batch_dfs.append(df)
            total += len(df)
        except Exception as e:
            failed.append((code, str(e)[:80]))

        if len(batch_dfs) >= batch_size:
            upsert_df(conn, pd.concat(batch_dfs, ignore_index=True), "index_minute_kline",
                      ["index_code", "trade_time", "k_type"], log=None)
            batch_dfs.clear()

        if i % 10 == 0 or i == len(codes):
            log.info(f"  进度: {i}/{len(codes)} ({i*100//len(codes)}%)  本次 +{total:,} 条")

    if batch_dfs:
        upsert_df(conn, pd.concat(batch_dfs, ignore_index=True), "index_minute_kline",
                  ["index_code", "trade_time", "k_type"], log=None)
    if failed:
        log.warning(f"失败 {len(failed)} 个，前 5: {failed[:5]}")
    return total


def run_all(datalen: int = 30, minute_datalen: int = 100):
    """一次性跑全部 5 个任务。"""
    log.info("=" * 60)
    log.info("开始每日增量更新 ...")
    update_stock_basic()
    update_trade_calendar()
    update_daily_kline(datalen=datalen)
    update_index_kline()
    update_minute_kline(datalen=minute_datalen)
    update_index_minute_kline(datalen=minute_datalen)
    log.info("每日增量更新完成")
    log.info("=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--datalen", type=int, default=30)
    parser.add_argument("--minute-datalen", type=int, default=100)
    parser.add_argument("--only", default="", help="basic,calendar,kline,index,minute,index_minute")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None
    if only is None or "basic" in only:
        update_stock_basic()
    if only is None or "calendar" in only:
        update_trade_calendar()
    if only is None or "kline" in only:
        update_daily_kline(datalen=args.datalen)
    if only is None or "index" in only:
        update_index_kline()
    if only is None or "minute" in only:
        update_minute_kline(datalen=args.minute_datalen)
    if only is None or "index_minute" in only:
        update_index_minute_kline(datalen=args.minute_datalen)
