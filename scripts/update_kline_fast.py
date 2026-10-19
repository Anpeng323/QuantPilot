"""
快速版每日增量更新（推荐日常唯一入口）。

相比 etl.daily_update 的旧串行/易卡死实现，本脚本：
- 复用 etl.daily_update 中已并发化（线程池 + 单只超时）的抓取函数，避免整进程卡死
- 覆盖全部日更任务：基础信息、交易日历、日K、指数K、5分钟K、指数5分钟K、
  市值快照、三表明细、产业链图谱、公告、因子重算
- 每个任务独立 try/except，单个任务（如 eastmoney 网络受限）失败不影响其余任务
- 支持 --only 选择部分任务、--no-system-proxy 绕过 macOS 系统代理直连

用法：
  python scripts/update_kline_fast.py
  python scripts/update_kline_fast.py --only kline,minute,factors
  python scripts/update_kline_fast.py --no-system-proxy
"""
from __future__ import annotations
import sys
import os
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from etl.utils import get_logger
from etl import daily_update as du
from analysis.factors import compute_basic_factors

log = get_logger("update_kline_fast")


def _maybe_disable_proxy():
    """绕过代理，强制直连。

    requests 可能从系统代理/环境变量/会话注入代理，仅清 env + trust_env 不够稳。
    这里直接 monkeypatch Session.get，强制每一次请求都不走代理（proxies={}），
    从根本上排除代理干扰。仅在使用 --no-system-proxy 时生效。
    """
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "no_proxy",
              "https_proxy", "http_proxy", "all_proxy"):
        os.environ.pop(k, None)
    requests.Session.trust_env = False
    _orig_get = requests.Session.get

    def _patched_get(self, *a, **kw):
        self.trust_env = False
        kw["proxies"] = {}
        return _orig_get(self, *a, **kw)

    requests.Session.get = _patched_get
    log.info("已禁用系统代理并 monkeypatch Session.get，强制直连")


def _build_tasks(args):
    """返回任务列表：(key, 描述, soft失败可继续, 调用函数)。"""
    return [
        ("basic",               "股票基础信息",      False, lambda: du.update_stock_basic()),
        ("calendar",            "交易日历",          False, lambda: du.update_trade_calendar()),
        ("kline",               "日K线",             False, lambda: du.update_daily_kline(datalen=args.datalen)),
        ("index",               "指数K线",           False, lambda: du.update_index_kline()),
        ("minute",              "5分钟K线",          False, lambda: du.update_minute_kline(datalen=args.minute_datalen)),
        ("index_minute",        "指数5分钟K线",      False, lambda: du.update_index_minute_kline(datalen=args.minute_datalen)),
        ("market_cap",          "市值快照(eastmoney)", True,  lambda: du.update_market_cap()),
        ("finance_statements",  "三表明细(eastmoney)", True,  lambda: du.update_finance_statements(per_night=args.per_night)),
        ("finance_abstract",    "财务摘要(sina)",      True,  lambda: du.update_finance_abstract(per_night=150)),
        ("industry_chain",      "产业链图谱(eastmoney)", True, lambda: du.update_industry_chain(per_night=args.chain_per_night)),
        ("announcement",        "公告(eastmoney)",    True,  lambda: du.update_announcement(days=args.ann_days)),
        ("factors",             "因子重算(本地)",      False, lambda: compute_basic_factors()),
    ]


def main():
    parser = argparse.ArgumentParser(description="快速版每日增量更新")
    parser.add_argument("--datalen", type=int, default=30)
    parser.add_argument("--minute-datalen", type=int, default=100)
    parser.add_argument("--per-night", type=int, default=50)
    parser.add_argument("--chain-per-night", type=int, default=30)
    parser.add_argument("--ann-days", type=int, default=1)
    parser.add_argument("--only", default="",
                        help="逗号分隔任务key，如 kline,minute,factors")
    parser.add_argument("--no-system-proxy", action="store_true",
                        help="绕过 macOS 系统代理直连（解决 eastmoney 走代理不可达问题）")
    args = parser.parse_args()

    if args.no_system_proxy:
        _maybe_disable_proxy()
        log.info("已禁用系统代理，直连模式")

    only = set(args.only.split(",")) if args.only else None
    tasks = _build_tasks(args)

    log.info("=" * 60)
    log.info("开始快速版每日增量更新 ...")
    t0 = time.time()
    ok, failed = [], []

    for key, desc, soft, fn in tasks:
        if only and key not in only:
            continue
        log.info(f"[开始] {desc} ({key})")
        try:
            n = fn()
            ok.append((key, desc, n))
            log.info(f"[完成] {desc}: {n}")
        except Exception as e:
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            failed.append((key, desc, msg))
            # soft 任务（eastmoney 网络受限等）仅警告跳过；其余也继续，避免单点失败中断全量更新
            log.warning(f"[跳过] {desc} 失败（不影响其余任务）: {msg}")

    log.info("=" * 60)
    log.info(f"快速版更新完成，用时 {round(time.time() - t0, 1)}s")
    log.info(f"成功 {len(ok)} 项: {[k for k, _, _ in ok]}")
    if failed:
        log.warning(f"失败/跳过 {len(failed)} 项:")
        for k, d, e in failed:
            log.warning(f"  - {d} ({k}): {e}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
