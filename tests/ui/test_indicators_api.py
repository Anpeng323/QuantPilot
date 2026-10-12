"""
TDD: 技术指标 API（覆盖 SPEC 3.4 新增的 kdj/arbr/adx/stochrsi + 指标元数据端点）。

约定（复用 tests/ui/test_api_routes.py 的 app_client fixture）：
- POST /api/v1/indicators/{name}/compute  body: {stock_code, params, days}
- GET  /api/v1/indicators                -> 指标名列表
- GET  /api/v1/indicators/{name}         -> 元数据 {name, params, param_specs}  （新增端点）
"""
import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def app_client(tmp_path):
    """独立的 FastAPI TestClient，使用临时 DuckDB（与 test_api_routes.py 同构）。"""
    from etl import db as db_mod
    original = db_mod.DB_PATH
    db_mod.DB_PATH = tmp_path / "test.duckdb"
    db_mod.init_schema()

    from web.app import create_app
    app = create_app()
    from fastapi.testclient import TestClient
    yield TestClient(app)

    db_mod.DB_PATH = original


def _seed_kline(conn, code: str = "000001", n: int = 60):
    np.random.seed(7)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    closes = 10 + np.cumsum(np.random.randn(n) * 0.1)
    rows = [
        (code, d, float(o), float(h), float(l), float(c))
        for d, o, h, l, c in zip(
            dates, closes - 0.1, closes + 0.2, closes - 0.2, closes
        )
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO daily_kline (stock_code, trade_date, open, high, low, close) "
            "VALUES (?,?,?,?,?,?)", r
        )


class TestIndicatorComputeAPI:
    def test_compute_kdj(self, app_client):
        from etl.db import get_conn
        _seed_kline(get_conn())
        r = app_client.post("/api/v1/indicators/kdj/compute", json={
            "stock_code": "000001", "params": {"n": 9}, "days": 30})
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data["rows"]) == 30
        row = data["rows"][0]
        assert "kdj_k" in row and "kdj_d" in row and "kdj_j" in row
        # KDJ 初始化为 50，不应是 null
        assert row["kdj_k"] is not None
        assert "close" in row

    def test_compute_arbr(self, app_client):
        from etl.db import get_conn
        _seed_kline(get_conn())
        r = app_client.post("/api/v1/indicators/arbr/compute", json={
            "stock_code": "000001", "params": {"n": 26}, "days": 40})
        assert r.status_code == 200, r.text
        data = r.json()
        row = data["rows"][0]
        assert "ar" in row and "br" in row
        # 前 25 行窗口不足 -> NaN -> null
        assert data["rows"][0]["ar"] is None

    def test_compute_adx(self, app_client):
        from etl.db import get_conn
        _seed_kline(get_conn())
        r = app_client.post("/api/v1/indicators/adx/compute", json={
            "stock_code": "000001", "params": {"n": 14}, "days": 40})
        assert r.status_code == 200, r.text
        data = r.json()
        row = data["rows"][0]
        assert "adx" in row and "plus_di" in row and "minus_di" in row
        # ADX 前 n-1=13 行为 NaN -> null
        assert data["rows"][0]["adx"] is None
        assert data["rows"][13]["adx"] is not None

    def test_compute_stochrsi(self, app_client):
        from etl.db import get_conn
        _seed_kline(get_conn())
        r = app_client.post("/api/v1/indicators/stochrsi/compute", json={
            "stock_code": "000001",
            "params": {"rsi_period": 14, "stoch_period": 14, "smooth": 3},
            "days": 40})
        assert r.status_code == 200, r.text
        data = r.json()
        row = data["rows"][0]
        assert "stochrsi" in row and "stochrsi_d" in row

    def test_unknown_indicator_404(self, app_client):
        r = app_client.post("/api/v1/indicators/does_not_exist/compute",
                            json={"stock_code": "000001"})
        assert r.status_code == 404

    def test_missing_stock_code_400(self, app_client):
        r = app_client.post("/api/v1/indicators/ma/compute", json={})
        assert r.status_code == 400


class TestIndicatorMetaAPI:
    def test_get_metadata(self, app_client):
        r = app_client.get("/api/v1/indicators/kdj")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["name"] == "kdj"
        assert "params" in data
        assert isinstance(data["param_specs"], list) and len(data["param_specs"]) >= 1
        spec_names = [s["name"] for s in data["param_specs"]]
        assert "n" in spec_names
        # 规格字段齐全，可驱动前端表单
        assert "type" in data["param_specs"][0]
        assert "default" in data["param_specs"][0]

    def test_metadata_unknown_404(self, app_client):
        r = app_client.get("/api/v1/indicators/does_not_exist")
        assert r.status_code == 404

    def test_list_includes_new_indicators(self, app_client):
        r = app_client.get("/api/v1/indicators")
        assert r.status_code == 200
        names = r.json()
        for n in ("ma", "macd", "boll", "rsi", "kdj", "arbr", "adx", "stochrsi"):
            assert n in names
