"""
T11 单元测试 · 回测 REST 接口（对应 spec/Phase1 §4 接口测试；T8 已落地）

通过 FastAPI TestClient 对 /api/v1/backtest/* 做端到端校验：
- 成功路径：返回 equity_curve / trades / metrics / data_flag，且交易非空、字段齐全。
- 异常路径：非法算子(400)、未知指标(400)、缺参(400)、无行情数据(400) 均映射正确。
- 辅助端点：/backtest/signal/validate 校验结构正确。

隔离方式：把 etl.db.DB_PATH 指向带样本日K的临时 DuckDB（不污染生产库）。
样本行情为确定性序列（前20日下跌、后40日强上涨），必然出现 ma5>ma20 金叉，
使引擎产生至少一笔买入交易。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from etl import db as etl_db
from web.app import create_app

TEST_STOCK = "TEST01"
VALID_SIGNAL = "ma5 > ma20"


def _build_sample_df() -> pd.DataFrame:
    """60 交易日：前 20 日下跌(20→10.5)，后 40 日强上涨(11→~42)，必然金叉。"""
    dates = pd.date_range("2023-01-02", periods=60, freq="B")
    closes = [20 - 0.5 * i for i in range(20)] + [11 + 0.8 * i for i in range(40)]
    opens = [c - 0.5 for c in closes]
    highs = [max(o, c) * 1.01 for o, c in zip(opens, closes)]
    lows = [min(o, c) * 0.99 for o, c in zip(opens, closes)]
    volumes = [1_000_000] * 60
    return pd.DataFrame({
        "trade_date": dates,
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
        "amount": [c * v for c, v in zip(closes, volumes)],
    })


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    # 1) 临时 DuckDB 并初始化 schema
    db_file = tmp_path / "test_quant.duckdb"
    monkeypatch.setattr(etl_db, "DB_PATH", db_file)
    etl_db.init_schema()

    # 2) 写入样本日K（adjust_type=0 / k_type=1，与 load_kline 回退逻辑一致）
    df = _build_sample_df()
    df["stock_code"] = TEST_STOCK
    df["turnover_ratio"] = 0.0
    df["adjust_type"] = 0
    df["trade_time"] = None
    df["k_type"] = 1
    con = duckdb.connect(str(db_file), read_only=False)
    con.register("sample", df)
    con.execute(
        "INSERT INTO daily_kline "
        "(stock_code, trade_date, open, high, low, close, volume, amount, "
        " turnover_ratio, adjust_type, trade_time, k_type) "
        "SELECT stock_code, trade_date, open, high, low, close, volume, amount, "
        "       turnover_ratio, adjust_type, trade_time, k_type FROM sample"
    )
    con.close()

    # 3) 构造 TestClient（路由在请求时才连 DB，已指向临时库）
    app = create_app()
    with TestClient(app) as client:
        yield client


def _run(client, payload: dict):
    return client.post("/api/v1/backtest/run", json=payload)


def test_backtest_run_success(api_client):
    resp = _run(api_client, {
        "stock_code": TEST_STOCK, "signal": VALID_SIGNAL,
        "start": "20230101", "end": "20231231",
        "initial_cash": 100000, "fill_price": "close",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for key in ("equity_curve", "trades", "metrics", "data_flag"):
        assert key in data, f"响应缺少 {key}"
    assert isinstance(data["trades"], list) and len(data["trades"]) >= 1
    buy = next((t for t in data["trades"] if t["side"] == "buy"), None)
    assert buy is not None, "应至少产生一笔买入"
    for f in ("date", "price", "quantity", "amount", "fee"):
        assert f in buy, f"买入交易缺字段 {f}"
    for m in ("total_return", "annual_return", "max_drawdown", "sharpe", "win_rate", "excess_return"):
        assert m in data["metrics"], f"metrics 缺字段 {m}"


def test_backtest_run_invalid_operator(api_client):
    resp = _run(api_client, {
        "stock_code": TEST_STOCK, "signal": "close ^ 5",
        "start": "20230101", "end": "20231231",
    })
    assert resp.status_code == 400
    assert "无法解析" in resp.json()["detail"]


def test_backtest_run_unknown_indicator(api_client):
    resp = _run(api_client, {
        "stock_code": TEST_STOCK, "signal": "xxx > 0",
        "start": "20230101", "end": "20231231",
    })
    assert resp.status_code == 400
    assert "未知指标列" in resp.json()["detail"]


def test_backtest_run_missing_params(api_client):
    resp = _run(api_client, {"stock_code": TEST_STOCK})  # 缺 signal/start/end
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "signal" in detail or "start" in detail


def test_backtest_run_no_data(api_client):
    resp = _run(api_client, {
        "stock_code": "NONEXIST", "signal": VALID_SIGNAL,
        "start": "20230101", "end": "20231231",
    })
    assert resp.status_code == 400
    assert "无足够行情" in resp.json()["detail"]


def test_backtest_validate_endpoint(api_client):
    ok = api_client.post("/api/v1/backtest/signal/validate", json={"signal": VALID_SIGNAL})
    assert ok.status_code == 200
    assert ok.json() == {"valid": True, "error": ""}

    bad = api_client.post("/api/v1/backtest/signal/validate", json={"signal": "close ^ 5"})
    assert bad.status_code == 200
    body = bad.json()
    assert body["valid"] is False and "无法解析" in body["error"]
