"""
TDD: Web 前端数据契约（integration）。

验证"平台无数据显示"的根因修复：
1. web/db.query_dict 必须返回【字典行】（列名作 key），否则前端 r.code / r.trade_date 全为 undefined
2. 启动的应用必须提供前端实际调用的端点，且响应字段与前端一致：
   - GET /api/snapshot            -> rows 为 dict，含 code/name/close/change_pct
   - GET /api/stock/info?q=       -> stock_code + last_close + last_date
   - GET /api/stock/kline?code=   -> rows 为 dict，含 trade_date/close
   - GET /api/indices/board       -> indices 列表，含 code/name/close/change_pct
   - GET /api/indices/kline?code= -> rows 为 dict，含 trade_date/close
   - POST /api/sql/query          -> rows 为 dict
   - GET /api/v1/indicators       -> 含 'kdj'

修复前应为 RED（端点缺失 + 返回数组行）。
"""
import pytest


def _seed(conn):
    conn.execute(
        "INSERT INTO stock_basic (stock_code, short_name, exchange, list_date) "
        "VALUES ('000001','测试银行','SZ','2020-01-01')"
    )
    dates = ['2024-01-08', '2024-01-09', '2024-01-10']
    closes = [10.0, 10.5, 11.0]
    for d, c in zip(dates, closes):
        conn.execute(
            "INSERT INTO daily_kline "
            "(stock_code, trade_date, k_type, open, high, low, close, pre_close, change, change_pct, volume, amount) "
            "VALUES (?,?,1,?,?,?,?,?,?,?,?,?)",
            ('000001', d, c - 0.1, c + 0.2, c - 0.2, c, c - 0.5, 0.5, 1.0, 1_000_000, 1_000_000_0),
        )
    conn.execute(
        "INSERT INTO index_kline "
        "(index_code, trade_date, open, high, low, close, volume, amount, change_pct) "
        "VALUES ('sh000001','2024-01-10',3000,3010,2990,3005,1e8,1e9,1.5)"
    )


@pytest.fixture
def app_client(tmp_path):
    from etl import db as db_mod
    import web.db as web_db_mod
    original = db_mod.DB_PATH
    db_mod.DB_PATH = tmp_path / "test.duckdb"
    db_mod.init_schema()
    # web 层在模块加载时把 DB_PATH 绑定为当时的值，这里强制同步到临时库并清掉线程连接缓存，
    # 确保无论导入顺序如何，应用都读到同一个临时库（与测试隔离，不依赖真实 data/quant.duckdb）。
    web_db_mod.DB_PATH = db_mod.DB_PATH
    web_db_mod._local.conn = None
    _seed(db_mod.get_conn())
    from web.app import create_app
    app = create_app()
    from fastapi.testclient import TestClient
    yield TestClient(app)
    db_mod.DB_PATH = original
    web_db_mod._local.conn = None


class TestWebDataContract:
    def test_snapshot_returns_dict_rows(self, app_client):
        r = app_client.get("/api/snapshot?limit=5")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["total"] >= 1
        row = data["rows"][0]
        assert isinstance(row, dict), "snapshot 行必须是字典（列名作 key）"
        assert "code" in row and "name" in row and "close" in row
        assert row["code"] == "000001"

    def test_stock_info_shape(self, app_client):
        r = app_client.get("/api/stock/info?q=000001")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["stock_code"] == "000001"
        assert d["last_close"] is not None
        assert d["last_date"] is not None

    def test_stock_kline_dict_rows(self, app_client):
        r = app_client.get("/api/stock/kline?code=000001&days=10")
        assert r.status_code == 200, r.text
        rows = r.json()["rows"]
        assert len(rows) >= 1
        assert isinstance(rows[0], dict)
        assert "trade_date" in rows[0] and "close" in rows[0]

    def test_indices_board_shape(self, app_client):
        r = app_client.get("/api/indices/board")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "indices" in data and len(data["indices"]) >= 1
        ind = data["indices"][0]
        assert "code" in ind and "name" in ind and "close" in ind and "change_pct" in ind

    def test_indices_kline_dict_rows(self, app_client):
        r = app_client.get("/api/indices/kline?code=sh000001&days=10")
        assert r.status_code == 200, r.text
        rows = r.json()["rows"]
        assert len(rows) >= 1
        assert isinstance(rows[0], dict)
        assert "trade_date" in rows[0] and "close" in rows[0]

    def test_sql_query_dict_rows(self, app_client):
        r = app_client.post("/api/sql/query", json={"query": "SELECT 1 AS x"})
        assert r.status_code == 200, r.text
        rows = r.json()["rows"]
        assert isinstance(rows[0], dict)
        assert rows[0]["x"] == 1

    def test_v1_indicators_listed(self, app_client):
        r = app_client.get("/api/v1/indicators")
        assert r.status_code == 200
        assert "kdj" in r.json()
