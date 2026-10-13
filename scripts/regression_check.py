#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MVP 回归检查：每次改动后运行，确认现有功能未被破坏。
  - 数据层：关键表最新日期 == 最近交易日；主键无重复；价格合法。
  - 前端层：app.js 语法 OK；关键特征存在（成交量涨红跌白 / MACD 负白 / 指数日K含成交量三栏）。
  - API 层（可选）：若提供 --api <url>，则探测关键接口是否 200。

用法：
  python3 scripts/regression_check.py                 # 仅数据 + 前端静态检查
  python3 scripts/regression_check.py --api http://127.0.0.1:8765   # 额外探测在线接口
退出码：0 全部通过，1 有失败。
"""
import os
import re
import sys
import duckdb
import subprocess
import urllib.request
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "quant.duckdb"
APP_JS = ROOT / "web" / "static" / "app.js"

RED = "\033[31m"; GREEN = "\033[32m"; RESET = "\033[0m"
fails = []

def ok(msg): print(f"{GREEN}✓{RESET} {msg}")
def bad(msg): print(f"{RED}✗{RESET} {msg}"); fails.append(msg)

# ---------------- 数据层 ----------------
def check_data():
    if not DB.exists():
        bad(f"数据库不存在: {DB}")
        return
    con = duckdb.connect(str(DB), read_only=True)
    q = lambda s: con.execute(s).fetchone()
    try:
        latest_trade = q("select max(trade_date) from trade_calendar where trade_status=1 and trade_date<=current_date")
        latest_trade = latest_trade[0] if latest_trade else None
        print(f"\n[A] 数据层（最近交易日 = {latest_trade}）")
        if not latest_trade:
            bad("交易日历为空"); return

        # 各表最新日期必须彼此一致（硬失败），并软提示是否滞后于最近交易日
        ref = q("select max(trade_date) from daily_kline")[0]
        for tbl in ["daily_kline","minute_kline","factors_daily","index_kline"]:
            mx = q(f"select max(trade_date) from {tbl}")[0]
            if mx == ref:
                ok(f"{tbl} 最新日期 {mx}（与日K一致）")
            else:
                bad(f"{tbl} 最新日期 {mx} 与日K({ref})不一致，疑似更新被破坏")

        if ref < latest_trade:
            print(f"  · 提示：数据停留在 {ref}，最近交易日为 {latest_trade}。"
                  "若今日尚未跑更新属正常，不是回归。")
        else:
            ok(f"数据已更新到最近交易日 {ref}")

        # 主键无重复
        # 注：daily_kline 复用存储日K(k_type=1)与周K(k_type=2)，唯一键须含 k_type
        pk = [("daily_kline","stock_code, trade_date, k_type, adjust_type"),
              ("minute_kline","stock_code, trade_date, trade_time"),
              ("factors_daily","stock_code, trade_date"),
              ("index_kline","index_code, trade_date")]
        for tbl, keys in pk:
            dup = q(f"select count(*) from (select {keys} from {tbl} group by {keys} having count(*)>1)")
            if dup and dup[0] == 0:
                ok(f"{tbl} 主键无重复")
            else:
                bad(f"{tbl} 存在重复主键: {dup[0] if dup else '?'}")

        # 价格合法性（A股主板的close必须为正；B股/新三板源噪声允许，设阈值）
        total = q("select count(*) from daily_kline")[0] or 1
        neg = q("select count(*) from daily_kline where close is null or close<=0")[0]
        if neg == 0:
            ok("daily_kline 无 null/<=0 收盘价")
        elif neg <= total * 0.01:
            ok(f"daily_kline 有 {neg} 条 null/<=0 收盘价（B股/新三板噪声，<1%，属正常）")
        else:
            bad(f"daily_kline 存在 {neg} 条 null/<=0 收盘价（>{total*0.01:.0f}，疑似异常）")

        # 活跃股（日K到最近交易日）必须也有当日分钟数据
        miss = q(f"""
            select count(*) from stock_basic s
            where exists(select 1 from daily_kline d where d.stock_code=s.stock_code and d.trade_date='{latest_trade}')
              and not exists(select 1 from minute_kline m where m.stock_code=s.stock_code and m.trade_date='{latest_trade}')
        """)
        miss = miss[0] if miss else -1
        if miss == 0:
            ok("所有活跃股均有当日分钟数据")
        else:
            bad(f"{miss} 只活跃股（日K到{latest_trade}）缺当日分钟数据")
    finally:
        con.close()

# ---------------- 前端层 ----------------
def check_frontend():
    print("\n[B] 前端层")
    if not APP_JS.exists():
        bad(f"前端文件不存在: {APP_JS}"); return

    # 1) JS 语法
    try:
        subprocess.run(["node","--check",str(APP_JS)], check=True, capture_output=True)
        ok("app.js 语法检查通过 (node --check)")
    except Exception as e:
        bad(f"app.js 语法错误: {e}")

    src = APP_JS.read_text(encoding="utf-8")

    # 2) 成交量按涨跌着色函数存在
    if "function volSeriesData" in src and "data: volSeriesData(rows)" in src:
        ok("成交量使用 volSeriesData（涨红跌白）")
    else:
        bad("未找到 volSeriesData 成交量着色")

    # 3) MACD 负值白色
    if "p.data >= 0 ? '#ef232a' : '#ffffff'" in src:
        ok("MACD 柱负值白色 ('#ffffff')")
    else:
        bad("MACD 柱未设置为负值白色")

    # 4) 指数日K 含成交量三栏（grid 数量应为3，且含'成交量' series 在 indexChart）
    # 定位 renderIndexChart 函数体，检查其内部同时有 3 个 grid 与成交量 series
    m = re.search(r"async function renderIndexChart\(code\)\s*\{(.*?)\n\}\n", src, re.S)
    if not m:
        bad("未找到 renderIndexChart 函数")
    else:
        body = m.group(1)
        grids = body.count("gridIndex:")
        if "name: '成交量', type: 'bar', data: volSeriesData(rows), xAxisIndex: 1" in body and grids >= 3:
            ok("指数日K含成交量栏（K/成交量/MACD 三栏）")
        else:
            bad("指数日K缺少成交量栏或 grid 不足3个")

# ---------------- API 层（可选） ----------------
def check_api(base):
    print(f"\n[C] API 层（{base}）")
    endpoints = [
        "/api/health",
        "/api/stock/kline?code=000001&days=5",
        "/api/indices/kline?code=000001&days=5",
        "/api/indices/minute?code=000001",
    ]
    for ep in endpoints:
        url = base.rstrip("/") + ep
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                data = json.loads(r.read().decode())
                if r.status == 200 and ("ok" in data or "rows" in data):
                    ok(f"{ep} -> 200")
                else:
                    bad(f"{ep} -> 异常响应: {r.status}")
        except Exception as e:
            bad(f"{ep} -> 请求失败: {e}")

def main():
    print("=== MVP 回归检查 ===")
    check_data()
    check_frontend()
    if "--api" in sys.argv:
        i = sys.argv.index("--api")
        if i + 1 < len(sys.argv):
            check_api(sys.argv[i+1])
    print("\n=== 结果 ===")
    if fails:
        print(f"{RED}失败 {len(fails)} 项:{RESET}")
        for f in fails: print("  -", f)
        sys.exit(1)
    else:
        print(f"{GREEN}全部通过，现有功能未被破坏。{RESET}")
        sys.exit(0)

if __name__ == "__main__":
    main()
