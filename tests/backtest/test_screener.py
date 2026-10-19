"""量化选股（backtest.screener）单元测试。

不依赖外部 LLM 与真实行情库：通过 monkeypatch 替换 build_universe / load_kline_bulk /
generate_code / execute_signal 来控制场景。
"""
from __future__ import annotations

from unittest import mock

import pandas as pd

from backtest.screener import build_universe, load_industry_map, run_screener
from backtest.sandbox import execute_signal, execute_signal_pooled, SandboxError


def _df(n=200, trend=1):
    close = [i * trend for i in range(n)]
    return pd.DataFrame(
        {
            "trade_date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": close, "high": close, "low": close, "close": close,
            "volume": [100] * n, "amount": [1000] * n,
        }
    )


CODE = """
def strategy(df, indicators, runtime):
    ma5 = df['close'].rolling(5).mean()
    ma20 = df['close'].rolling(20).mean()
    signal = ma5 > ma20
    return signal
"""


def test_execute_signal_capture_debug():
    sig, logs, dbg = execute_signal(CODE, _df(30, 1), capture_debug=True)
    assert isinstance(sig, list) and len(sig) == 30
    assert isinstance(dbg, dict)
    assert dbg.get("ma5") is not None and dbg.get("ma20") is not None


def test_execute_signal_backward_compat_no_debug():
    # 不请求 debug 时仍返回 (signal_list, logs) 二元组，不影响现有调用方
    sig, logs = execute_signal(CODE, _df(30, 1), capture_debug=False)
    assert isinstance(sig, list) and len(sig) == 30


def test_build_universe_excludes_st_and_recent():
    rows = [
        ("600519", "贵州茅台", "sh", "2020-01-01"),
        ("000001", "*ST风险股", "sz", "2020-01-01"),   # ST 应剔除
        ("688001", "次新股", "sh", "2026-01-01"),       # 上市不足1年应剔除
    ]
    fake = mock.MagicMock()
    fake.execute.return_value.fetchall.return_value = rows
    with mock.patch("backtest.screener.get_conn", return_value=fake), \
         mock.patch("backtest.screener._latest_trade_date", return_value="2026-10-14"):
        uni = build_universe()
    codes = [u["stock_code"] for u in uni]
    assert "600519" in codes
    assert "000001" not in codes
    assert "688001" not in codes


def test_load_industry_map_prefers_primary_and_falls_back():
    rows = [
        ("A", "银行", "申万一级"),
        ("A", "股份制银行Ⅱ", "申万二级"),   # 申万一级已存在，忽略申万二级
        ("B", "白酒Ⅱ", "申万二级"),         # 无申万一级，退回到申万二级
        ("C", None, "申万一级"),             # 行业名缺失，跳过
        # adata 仅提供国民经济分类（无申万）：取更具体的「二级」，而非笼统「一级」
        ("D", "制造业", "一级"),
        ("D", "造纸和纸制品业", "二级"),
    ]
    fake = mock.MagicMock()
    fake.execute.return_value.fetchall.return_value = rows
    with mock.patch("backtest.screener.get_conn", return_value=fake):
        m = load_industry_map()
    assert m == {"A": "银行", "B": "白酒Ⅱ", "D": "造纸和纸制品业"}


def test_run_screener_last_day_hit_and_skip():
    uni = [
        {"stock_code": "A", "short_name": "升", "exchange": "sh", "list_date": "2020-01-01"},
        {"stock_code": "B", "short_name": "跌", "exchange": "sz", "list_date": "2020-01-01"},
    ]
    bulk = {"A": _df(200, 1), "B": _df(200, -1)}  # A 上升(ma5>ma20)，B 下降

    with mock.patch("backtest.screener.build_universe", return_value=uni), \
         mock.patch("backtest.screener.load_kline_bulk", return_value=bulk), \
         mock.patch("backtest.screener.load_industry_map", return_value={"A": "银行"}), \
         mock.patch("backtest.screener.generate_code", return_value=(CODE, None)):
        res = run_screener("5日均线上穿20日均线")

    assert res["ok"] is True
    codes = [m["stock_code"] for m in res["matches"]]
    assert codes == ["A"]  # 仅最新交易日信号为 True 的 A 命中
    assert res["total"] == 2
    assert res["matches"][0]["industry"] == "银行"  # 结果表带板块列


def test_run_screener_single_stock_error_does_not_abort():
    uni = [
        {"stock_code": "A", "short_name": "升", "exchange": "sh", "list_date": "2020-01-01"},
        {"stock_code": "B", "short_name": "跌", "exchange": "sz", "list_date": "2020-01-01"},
    ]
    bulk = {"A": _df(200, 1), "B": _df(200, -1)}

    def boom(code, df, timeout=20, capture_debug=False):
        if df is bulk["A"]:
            raise SandboxError("模拟个股异常")
        return execute_signal(code, df, capture_debug=capture_debug)

    with mock.patch("backtest.screener.build_universe", return_value=uni), \
         mock.patch("backtest.screener.load_kline_bulk", return_value=bulk), \
         mock.patch("backtest.screener.load_industry_map", return_value={}), \
         mock.patch("backtest.screener.generate_code", return_value=(CODE, None)), \
         mock.patch("backtest.screener.execute_signal_pooled", side_effect=boom):
        res = run_screener("x")

    assert res["ok"] is True
    assert res["matches"] == []          # A 失败、B 不满足 → 无命中
    assert res["skipped"] >= 1           # A 计为跳过，整体不中断


def test_run_screener_empty_nl():
    res = run_screener("   ")
    assert res["ok"] is False
    assert res["matches"] == []
