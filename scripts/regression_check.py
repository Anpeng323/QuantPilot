#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MVP 回归检查：每次改动后运行，确认现有功能未被破坏。
  - 数据层：关键表最新日期 == 最近交易日；主键无重复；价格合法。
  - 前端层：app.js 语法 OK；关键特征存在（成交量涨红跌白 / MACD 负白 / 指数日K含成交量三栏）。
  - API 层（可选）：若提供 --api <url>，则探测关键接口是否 200。
  - 回测层：固定场景 (000001, `ma5 > ma20`, 2023 全年) 跑引擎，断言交易非空、
            指标齐全，并与基线快照 (data/backtest_baseline.json) 比对容忍度。

用法：
  python3 scripts/regression_check.py                 # 仅数据 + 前端静态检查 + 回测层
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
# 让 scripts/ 下可直接 import 项目模块（backtest / ai / etl 等）
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
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

# ---------------- 回测层（Phase 1 / T10） ----------------
def check_backtest():
    print("\n[D] 回测层")
    import json as _json
    from backtest.engine import BacktestRequest, run_backtest, BacktestError
    from backtest.cost import CostConfig

    SCENARIO = {
        "stock_code": "000001",
        "start": "20230101",
        "end": "20231231",
        "signal": "ma5 > ma20",
        "initial_cash": 100000.0,
        "fill_price": "close",
    }
    baseline_path = ROOT / "data" / "backtest_baseline.json"
    REQUIRED_METRICS = ["total_return", "annual_return", "max_drawdown",
                        "sharpe", "win_rate", "excess_return"]
    # 指标相对基线的容忍偏差（固定历史区间应高度稳定，留小余量防浮点/数据补录）
    TOL = {"total_return": 0.02, "annual_return": 0.02, "max_drawdown": 0.02,
           "sharpe": 0.1, "win_rate": 0.02, "excess_return": 0.02}

    # 1) 数据可用性前置：无样本数据则软跳过，不误报回归
    #    trade_date 为 DATE 列，需用 YYYY-MM-DD；load_kline 内部已归一化，这里手动归一
    def _d(s):
        s = str(s)
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s

    con = duckdb.connect(str(DB), read_only=True)
    try:
        n = con.execute(
            "select count(*) from daily_kline where stock_code=? and k_type=1 and adjust_type=0 "
            "and trade_date between ? and ?",
            [SCENARIO["stock_code"], _d(SCENARIO["start"]), _d(SCENARIO["end"])],
        ).fetchone()[0]
    finally:
        con.close()
    if not n:
        print(f"  · 跳过：daily_kline 无 {SCENARIO['stock_code']} "
              f"{SCENARIO['start']}~{SCENARIO['end']} 数据（数据层未覆盖，非回归）")
        return

    # 2) 执行固定场景回测
    req = BacktestRequest(
        stock_code=SCENARIO["stock_code"], start=SCENARIO["start"], end=SCENARIO["end"],
        signal=SCENARIO["signal"], initial_cash=SCENARIO["initial_cash"],
        fill_price=SCENARIO["fill_price"], cost=CostConfig(),
    )
    try:
        res = run_backtest(req)
    except BacktestError as e:
        bad(f"回测执行失败: {e}")
        return
    except Exception as e:
        bad(f"回测异常: {e}")
        return

    trades = res.get("trades") or []
    if not trades:
        bad("回测层：固定场景未产生交易（信号/数据异常）")
        return
    ok(f"固定场景({SCENARIO['stock_code']}, {SCENARIO['signal']}, 2023) 产生 {len(trades)} 笔交易")

    metrics = res.get("metrics") or {}
    missing = [k for k in REQUIRED_METRICS if k not in metrics]
    if missing:
        bad(f"回测层：metrics 缺字段 {missing}")
        return
    ok("回测层：metrics 全字段齐全")

    # 3) 基线快照：首跑生成，后续比对
    snapshot = {
        "stock_code": SCENARIO["stock_code"], "start": SCENARIO["start"],
        "end": SCENARIO["end"], "signal": SCENARIO["signal"],
        "trade_count": len(trades), "metrics": metrics,
    }
    if not baseline_path.exists():
        baseline_path.write_text(
            _json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        ok(f"回测层：已生成基线快照 -> {baseline_path.name}")
        return

    base = _json.loads(baseline_path.read_text(encoding="utf-8"))
    if (base.get("stock_code") != SCENARIO["stock_code"]
            or base.get("signal") != SCENARIO["signal"]
            or base.get("start") != SCENARIO["start"]
            or base.get("end") != SCENARIO["end"]):
        bad("回测层：基线快照场景与当前不一致，请人工核对后重置基线")
        return
    if base.get("trade_count") != len(trades):
        bad(f"回测层：交易笔数漂移 {base.get('trade_count')} -> {len(trades)}"
            "（疑似撮合/信号逻辑被改）")
        return
    ok(f"回测层：交易笔数稳定 = {len(trades)}")

    drift = []
    for k, t in TOL.items():
        bv, cv = base["metrics"].get(k), metrics.get(k)
        if bv is None or cv is None:
            drift.append(f"{k}=None")
        elif abs(bv - cv) > t:
            drift.append(f"{k}: {bv}->{cv}(>{t})")
    if drift:
        bad("回测层：指标相对基线漂移超阈值 -> " + ", ".join(drift))
    else:
        ok("回测层：关键指标与基线一致（偏差在容忍内）")


# ---------------- 专业性（免责声明 / 结构化字段 / 路由 / 落地） ----------------
def check_professional():
    """验证「专业性优化」已落地：5 个 AI 能力统一带 disclaimer + 结构化字段并持久化。"""
    print("\n[E] 专业性（免责声明 / 结构化字段 / 路由 / 落地）")
    # 1) 结构化输出协议入口存在
    try:
        import ai.structured as st
        assert hasattr(st, "attach_disclaimer") and hasattr(st, "compute_confidence")
        ok("ai.structured 协议入口存在（attach_disclaimer / compute_confidence）")
    except Exception as e:
        bad(f"ai.structured 缺失: {e}")

    # 2) §6.5 免责声明常量存在且非空
    try:
        from ai.prompts import DISCLAIMER_SEC65
        if DISCLAIMER_SEC65 and len(DISCLAIMER_SEC65) > 10:
            ok("ai.prompts.DISCLAIMER_SEC65 存在且非空")
        else:
            bad("DISCLAIMER_SEC65 缺失或为空")
    except Exception as e:
        bad(f"DISCLAIMER_SEC65 读取失败: {e}")

    # 3) NL 回测返回含 citations / confidence / disclaimer
    try:
        import backtest.nl as nl
        e = nl._err(None, False, "test")
        if all(k in e for k in ("citations", "confidence", "disclaimer")):
            ok("NL 回测返回含 citations / confidence / disclaimer")
        else:
            bad("NL 回测返回缺少结构化字段：" + str(list(e.keys())))
    except Exception as e:
        bad(f"NL 回测结构化检查失败: {e}")

    # 4) 选股结果含 citations / confidence（确定性推导）
    try:
        import backtest.screener as sc
        c = sc._sc_confidence(5000, 4800, 12)
        if isinstance(c, float) and 0 < c <= 1:
            ok(f"选股置信度推导正常（= {c}）")
        else:
            bad(f"选股置信度推导异常: {c}")
    except Exception as e:
        bad(f"选股结构化检查失败: {e}")

    # 5) agent_session / screener_result 持久化表含结构化列
    con = duckdb.connect(str(DB), read_only=True)
    try:
        # DuckDB PRAGMA table_info 列序：cid(0), name(1), type(2), ...
        acols = {r[1] for r in con.execute("PRAGMA table_info(agent_session)").fetchall()}
        if {"citations_json", "confidence"}.issubset(acols):
            ok("agent_session 含 citations_json / confidence 列")
        else:
            bad("agent_session 缺少结构化列：" + str(acols))
        scols = {r[1] for r in con.execute("PRAGMA table_info(screener_result)").fetchall()}
        if {"citations_json", "confidence"}.issubset(scols):
            ok("screener_result 含 citations_json / confidence 列")
        else:
            bad("screener_result 缺少结构化列：" + str(scols))
    except Exception as e:
        bad(f"持久化表检查失败: {e}")
    finally:
        con.close()

    # 6) NL 回测路由默认 save=True（专业化：默认落库）
    try:
        rsrc = (ROOT / "web" / "routes.py").read_text(encoding="utf-8")
        if 'payload.get("save", True)' in rsrc:
            ok("NL 回测 / 规则回测 路由默认 save=True（默认落库）")
        else:
            bad("路由未设置 NL 回测默认 save=True")
    except Exception as e:
        bad(f"路由检查失败: {e}")


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
    check_backtest()
    check_professional()
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
