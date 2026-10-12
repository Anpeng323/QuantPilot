"""
A. 一次性回填分钟 K 线（5min 默认）
B. 每日增量（每天收盘后追加）

设计：
- 数据源：新浪分钟线（datalen ≤ 1500，对 5min 约 31 天）
- 一次性回填：拉每只票最近 datalen 根 5min K（~30 天）
- 每日增量：每个交易日拉最新 100 根 5min K（~2 天覆盖）
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from etl.db import get_conn
from etl.utils import get_logger, throttle, job_runner, upsert_df
from etl.fetchers import fetch_sina_kline

log = get_logger("minute")


@throttle(seconds=0.05)
def fetch_one_minute(stock_code: str, k_type: int, datalen: int) -> pd.DataFrame:
    df = fetch_sina_kline(stock_code, k_type=k_type, datalen=datalen)
    if df.empty:
        return df
    df["stock_code"] = stock_code
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["trade_time"] = pd.to_datetime(df["trade_time"])
    df["k_type"]     = k_type
    return df[["stock_code", "trade_time", "trade_date", "k_type",
               "open", "high", "low", "close", "volume", "amount"]]


@job_runner("minute.backfill", table_name="minute_kline")
def backfill_minute(conn, k_type: int = 5, datalen: int = 1500,
                    limit: int | None = None,
                    only_codes: list[str] | None = None,
                    batch_size: int = 200) -> int:
    """一次性回填近 ~30 天的 5min K 线（datalen=1500 ≈ 31 个交易日）。"""
    codes = [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_basic ORDER BY stock_code"
    ).fetchall()]
    if only_codes:
        codes = only_codes
    if limit:
        codes = codes[:limit]

    log.info(f"准备拉 {len(codes)} 只票的 {k_type}min K 线（datalen={datalen}）")
    total = 0
    failed = []
    batch_dfs = []

    for i, code in enumerate(codes, 1):
        try:
            df = fetch_one_minute(code, k_type, datalen)
            if df.empty:
                continue
            batch_dfs.append(df)
            total += len(df)
        except Exception as e:
            failed.append((code, str(e)[:80]))

        if len(batch_dfs) >= batch_size:
            upsert_df(conn, pd.concat(batch_dfs, ignore_index=True), "minute_kline",
                      ["stock_code", "trade_time", "k_type"], log=None)
            batch_dfs.clear()

        if i % 200 == 0 or i == len(codes):
            log.info(f"  进度: {i}/{len(codes)} ({i*100//len(codes)}%)  累计 {total:,} 条")

    if batch_dfs:
        upsert_df(conn, pd.concat(batch_dfs, ignore_index=True), "minute_kline",
                  ["stock_code", "trade_time", "k_type"], log=None)

    if failed:
        log.warning(f"失败 {len(failed)} 只，前 5: {failed[:5]}")
    return total


@job_runner("minute.daily_update", table_name="minute_kline")
def daily_update_minute(conn, k_type: int = 5, datalen: int = 100,
                        batch_size: int = 300) -> int:
    """
    每日增量：每只票拉最近 datalen 根 5min K（约 2 天），upsert 覆盖。
    5640 只 × 100 根 ≈ 56 万行，约 8-10 分钟跑完。
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
            df = fetch_one_minute(code, k_type, datalen)
            if df.empty:
                continue
            batch_dfs.append(df)
            total += len(df)
        except Exception as e:
            failed.append((code, str(e)[:80]))

        if len(batch_dfs) >= batch_size:
            upsert_df(conn, pd.concat(batch_dfs, ignore_index=True), "minute_kline",
                      ["stock_code", "trade_time", "k_type"], log=None)
            batch_dfs.clear()

        if i % 500 == 0 or i == len(codes):
            log.info(f"  进度: {i}/{len(codes)} ({i*100//len(codes)}%)  本次 +{total:,} 条")

    if batch_dfs:
        upsert_df(conn, pd.concat(batch_dfs, ignore_index=True), "minute_kline",
                  ["stock_code", "trade_time", "k_type"], log=None)

    if failed:
        log.warning(f"失败 {len(failed)} 只，前 5: {failed[:5]}")
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["backfill", "daily"], default="backfill")
    parser.add_argument("--k-type", type=int, default=5)
    parser.add_argument("--datalen", type=int, default=1500)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--codes", nargs="*", default=None)
    args = parser.parse_args()

    if args.mode == "backfill":
        backfill_minute(k_type=args.k_type, datalen=args.datalen,
                        limit=args.limit or None, only_codes=args.codes)
    else:
        daily_update_minute(k_type=args.k_type, datalen=min(args.datalen, 240))
