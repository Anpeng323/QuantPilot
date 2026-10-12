"""
TDD: 技术指标前端接线契约（integration）。

保证前端（web/static）满足：
- 指标面板元素齐全（代码框 / 指标下拉 / 计算按钮 / 图表 / 动态参数表单）
- app.js 通过 API 动态填充指标列表、根据 param_specs 生成参数表单
- app.js 能渲染 4 个新指标（kdj/arbr/adx/stochrsi）

这些在改动前端前应为 RED。
"""
import pytest


@pytest.fixture
def app_client(tmp_path):
    from etl import db as db_mod
    original = db_mod.DB_PATH
    db_mod.DB_PATH = tmp_path / "test.duckdb"
    db_mod.init_schema()
    from web.app import create_app
    app = create_app()
    from fastapi.testclient import TestClient
    yield TestClient(app)
    db_mod.DB_PATH = original


class TestIndicatorFrontend:
    def test_panel_elements_present(self, app_client):
        r = app_client.get("/")
        assert r.status_code == 200
        html = r.text
        for el in ("ind-stock", "ind-name", "ind-compute",
                  "ind-chart", "ind-table", "ind-params-form"):
            assert f'id="{el}"' in html, f"指标面板缺少元素 {el}"

    def test_js_dynamic_indicator_wiring(self, app_client):
        r = app_client.get("/static/app.js")
        assert r.status_code == 200
        js = r.text
        # 动态填充指标列表 + 按 param_specs 生成表单 + 4 个新指标渲染
        assert "loadIndicatorList" in js, "app.js 缺少 loadIndicatorList"
        assert "loadIndicatorParams" in js, "app.js 缺少 loadIndicatorParams"
        assert "param_specs" in js, "app.js 未消费元数据端点"
        for n in ("kdj", "arbr", "adx", "stochrsi"):
            assert n in js, f"app.js 未处理指标 {n}"
