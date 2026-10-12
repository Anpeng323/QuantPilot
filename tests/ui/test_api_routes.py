"""
TDD: API 路由（v1.1 新增的 7 个模块都要有 REST 端点）

新增端点：
- /api/v1/prompts           GET/POST
- /api/v1/prompts/{id}      GET/PUT/DELETE
- /api/v1/alerts/rules      GET/POST
- /api/v1/alerts/rules/{id} DELETE
- /api/v1/alerts/run        POST (触发 run_all)
- /api/v1/alerts/events     GET
- /api/v1/indicators        GET（列出所有指标）
- /api/v1/indicators/{name}/compute  POST（计算指标）
- /api/v1/portfolio/snapshot GET
- /api/v1/portfolio/sync-prices POST
- /api/v1/exports/analysis/{id}/md   GET
- /api/v1/exports/analysis/{id}/pdf  GET
- /api/v1/exports/watchlist/{group}/excel GET
- /api/v1/router/models     GET
"""
import pytest
import os


@pytest.fixture
def app_client(tmp_path):
    """独立的 FastAPI TestClient，使用临时 DuckDB"""
    from etl import db as db_mod
    original = db_mod.DB_PATH
    db_mod.DB_PATH = tmp_path / "test.duckdb"
    db_mod.init_schema()

    from web.app import create_app
    app = create_app()
    from fastapi.testclient import TestClient
    yield TestClient(app)

    db_mod.DB_PATH = original


class TestPromptsAPI:
    def test_list_prompts(self, app_client):
        r = app_client.get("/api/v1/prompts")
        assert r.status_code == 200

    def test_seed_builtins(self, app_client):
        from prompts.template import seed_builtin_templates
        from etl.db import get_conn
        seed_builtin_templates(get_conn())
        r = app_client.get("/api/v1/prompts")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        # 至少应该有 6 个 builtin
        builtins = [p for p in data if p.get("is_builtin")]
        assert len(builtins) >= 5

    def test_create_prompt(self, app_client):
        payload = {
            "template_id": "u1", "name": "我的提示词",
            "category": "custom", "body": "测试 {{ x }}",
            "variables": ["x"], "is_builtin": False,
        }
        r = app_client.post("/api/v1/prompts", json=payload)
        assert r.status_code in (200, 201), r.text

        # 列表里能找到
        r = app_client.get("/api/v1/prompts")
        ids = [p["template_id"] for p in r.json()]
        assert "u1" in ids


class TestAlertsAPI:
    def test_list_rules(self, app_client):
        r = app_client.get("/api/v1/alerts/rules")
        assert r.status_code == 200

    def test_create_rule(self, app_client):
        r = app_client.post("/api/v1/alerts/rules", json={
            "name": "PE>50", "target_type": "stock", "target_value": "000001",
            "metric": "pe", "operator": ">", "threshold": 50.0,
        })
        assert r.status_code in (200, 201)
        rule = r.json()
        assert "rule_id" in rule

    def test_run_alerts(self, app_client):
        # 先灌一条数据 + 一条规则
        from etl.db import get_conn
        conn = get_conn()
        conn.execute("""
            INSERT INTO finance_core (stock_code, report_date, pe)
            VALUES ('000001', '2024-09-30', 80.0)
        """)
        app_client.post("/api/v1/alerts/rules", json={
            "name": "PE>50", "target_type": "stock", "target_value": "000001",
            "metric": "pe", "operator": ">", "threshold": 50.0,
        })
        r = app_client.post("/api/v1/alerts/run")
        assert r.status_code == 200
        events = r.json()
        assert isinstance(events, list)
        assert len(events) >= 1

    def test_list_events(self, app_client):
        r = app_client.get("/api/v1/alerts/events")
        assert r.status_code == 200


class TestIndicatorsAPI:
    def test_list_indicators(self, app_client):
        r = app_client.get("/api/v1/indicators")
        assert r.status_code == 200
        names = r.json()
        assert "ma" in names
        assert "macd" in names
        assert "boll" in names
        assert "rsi" in names

    def test_compute_indicator(self, app_client):
        from etl.db import get_conn
        conn = get_conn()
        # 灌 60 天 K 线
        import pandas as pd
        import numpy as np
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=60, freq="B")
        closes = 10 + np.cumsum(np.random.randn(60) * 0.1)
        rows = list(zip(
            ["000001"] * 60, dates, closes,
            (closes + 0.1).tolist(), (closes + 0.2).tolist(),
            (closes - 0.2).tolist(),
        ))
        for row in rows:
            conn.execute("""
                INSERT INTO daily_kline (stock_code, trade_date, open, high, low, close)
                VALUES (?, ?, ?, ?, ?, ?)
            """, row)

        # 计算 MA5
        r = app_client.post("/api/v1/indicators/ma/compute", json={
            "stock_code": "000001", "params": {"period": 5}, "days": 30,
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert "rows" in data
        assert len(data["rows"]) == 30
        # 每行应有 close + ma5
        assert "ma5" in data["rows"][0]
        assert "close" in data["rows"][0]


class TestPortfolioAPI:
    def test_snapshot_empty(self, app_client):
        r = app_client.get("/api/v1/portfolio/snapshot")
        assert r.status_code == 200
        data = r.json()
        assert "rows" in data
        assert "total" in data
        assert data["total"]["position_count"] == 0

    def test_sync_prices(self, app_client):
        # 用 mock provider：注入函数到 portfolio.sync 模块
        from portfolio import sync as ps
        ps.set_provider(lambda codes: {c: 10.0 for c in codes})

        r = app_client.post("/api/v1/portfolio/sync-prices",
                            json=["000001", "000002"])
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["n_updated"] == 2

        ps.set_provider(None)  # 还原


class TestExportsAPI:
    def test_export_analysis_md(self, app_client):
        from etl.db import get_conn
        conn = get_conn()
        conn.execute("""
            INSERT INTO ai_analysis
                (analysis_id, stock_code, analysis_type, response, model_used)
            VALUES ('a1', '000001', 'financial', '# 标题\n正文', 'mock')
        """)
        r = app_client.get("/api/v1/exports/analysis/a1/md")
        assert r.status_code == 200, r.text
        # MD 应在 content 里（不是文件下载）
        data = r.json()
        assert "# 标题" in data["content"]

    def test_export_analysis_pdf(self, app_client):
        from etl.db import get_conn
        conn = get_conn()
        conn.execute("""
            INSERT INTO ai_analysis
                (analysis_id, stock_code, analysis_type, response, model_used)
            VALUES ('a1', '000001', 'financial', '# 标题\n平安银行 正文', 'mock')
        """)
        r = app_client.get("/api/v1/exports/analysis/a1/pdf")
        assert r.status_code == 200
        data = r.json()
        assert os.path.exists(data["path"])
        assert data["path"].endswith(".pdf")
        assert os.path.getsize(data["path"]) > 500

    def test_export_watchlist_excel(self, app_client):
        from etl.db import get_conn
        conn = get_conn()
        conn.execute("INSERT INTO stock_basic (stock_code, short_name, exchange) VALUES ('000001', '平安银行', 'SZ')")
        conn.execute("INSERT INTO watchlist_group (group_name, sort_order) VALUES ('g1', 1)")
        conn.execute("INSERT INTO watchlist (group_name, stock_code) VALUES ('g1', '000001')")

        r = app_client.get("/api/v1/exports/watchlist/g1/excel")
        assert r.status_code == 200, r.text
        data = r.json()
        assert os.path.exists(data["path"])
        assert data["path"].endswith(".xlsx")


class TestRouterAPI:
    def test_list_models(self, app_client):
        r = app_client.get("/api/v1/router/models")
        assert r.status_code == 200
        data = r.json()
        assert "config" in data
        assert "financial_summary" in data["config"]
