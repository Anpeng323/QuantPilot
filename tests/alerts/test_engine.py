"""
TDD: 预警引擎（AlertEngine + AlertRule CRUD）
"""
import pytest
from datetime import date


def _seed_basic_finance(conn, stock_code="000001", pe=15.0, pb=2.4):
    conn.execute("""
        INSERT INTO finance_core (stock_code, report_date, pe, pb)
        VALUES (?, ?, ?, ?)
    """, [stock_code, date(2024, 9, 30), pe, pb])


def _seed_basic_kline(conn, stock_code="000001", trade_date=date(2024, 12, 31), change_pct=2.5):
    conn.execute("""
        INSERT INTO daily_kline (stock_code, trade_date, close, change_pct)
        VALUES (?, ?, ?, ?)
    """, [stock_code, trade_date, 10.0, change_pct])


class TestAlertRuleCRUD:
    def test_create_and_get(self, empty_duckdb_conn):
        from alerts.engine import AlertRule

        r = AlertRule.create(
            conn=empty_duckdb_conn,
            rule_id="r1",
            name="PE 过高",
            target_type="stock",
            target_value="000001",
            metric="pe",
            operator=">",
            threshold=50.0,
            channel="web",
        )
        assert r.rule_id == "r1"
        assert r.metric == "pe"
        assert r.operator == ">"
        assert r.threshold == 50.0

        got = AlertRule.get(empty_duckdb_conn, "r1")
        assert got is not None
        assert got.name == "PE 过高"

    def test_create_with_default_id(self, empty_duckdb_conn):
        from alerts.engine import AlertRule
        r = AlertRule.create(
            conn=empty_duckdb_conn, name="x", metric="pe", operator=">", threshold=10.0,
        )
        assert r.rule_id is not None and len(r.rule_id) > 0

    def test_duplicate_id_raises(self, empty_duckdb_conn):
        from alerts.engine import AlertRule
        AlertRule.create(conn=empty_duckdb_conn, rule_id="dup", name="A", metric="pe", operator=">", threshold=10.0)
        with pytest.raises(ValueError):
            AlertRule.create(conn=empty_duckdb_conn, rule_id="dup", name="B", metric="pe", operator=">", threshold=10.0)

    def test_list_active(self, empty_duckdb_conn):
        from alerts.engine import AlertRule
        AlertRule.create(conn=empty_duckdb_conn, rule_id="a", name="A", metric="pe", operator=">", threshold=10, is_active=True)
        AlertRule.create(conn=empty_duckdb_conn, rule_id="b", name="B", metric="pe", operator=">", threshold=10, is_active=False)
        active = AlertRule.list_active(empty_duckdb_conn)
        assert len(active) == 1
        assert active[0].rule_id == "a"

    def test_delete(self, empty_duckdb_conn):
        from alerts.engine import AlertRule
        AlertRule.create(conn=empty_duckdb_conn, rule_id="d", name="D", metric="pe", operator=">", threshold=10)
        AlertRule.delete(empty_duckdb_conn, "d")
        assert AlertRule.get(empty_duckdb_conn, "d") is None

    def test_unknown_metric_raises(self, empty_duckdb_conn):
        from alerts.engine import AlertRule
        with pytest.raises(ValueError, match="未知 metric"):
            AlertRule.create(
                conn=empty_duckdb_conn, rule_id="r1", name="x",
                target_type="stock", target_value="000001",
                metric="nonsense_metric", operator=">", threshold=10.0,
            )

    def test_unknown_operator_raises(self, empty_duckdb_conn):
        from alerts.engine import AlertRule
        with pytest.raises(ValueError, match="未知 operator"):
            AlertRule.create(
                conn=empty_duckdb_conn, rule_id="r1", name="x",
                target_type="stock", target_value="000001",
                metric="pe", operator="???", threshold=10.0,
            )


class TestAlertEvaluate:
    def test_evaluate_pe_over_threshold(self, empty_duckdb_conn):
        from alerts.engine import AlertRule, AlertEngine
        _seed_basic_finance(empty_duckdb_conn, stock_code="000001", pe=80.0)

        rule = AlertRule.create(
            conn=empty_duckdb_conn, rule_id="r1", name="PE>50",
            target_type="stock", target_value="000001",
            metric="pe", operator=">", threshold=50.0, channel="web",
        )
        triggered = AlertEngine.evaluate_rule(empty_duckdb_conn, rule)
        assert len(triggered) == 1
        ev = triggered[0]
        assert ev.rule_id == "r1"
        assert ev.value == 80.0
        assert "000001" in ev.message

    def test_evaluate_pe_not_triggered(self, empty_duckdb_conn):
        from alerts.engine import AlertRule, AlertEngine
        _seed_basic_finance(empty_duckdb_conn, stock_code="000001", pe=20.0)

        rule = AlertRule.create(
            conn=empty_duckdb_conn, rule_id="r1", name="PE>50",
            target_type="stock", target_value="000001",
            metric="pe", operator=">", threshold=50.0, channel="web",
        )
        triggered = AlertEngine.evaluate_rule(empty_duckdb_conn, rule)
        assert triggered == []

    def test_evaluate_pe_below(self, empty_duckdb_conn):
        from alerts.engine import AlertRule, AlertEngine
        _seed_basic_finance(empty_duckdb_conn, stock_code="000001", pe=10.0)

        rule = AlertRule.create(
            conn=empty_duckdb_conn, rule_id="r2", name="PE<15",
            target_type="stock", target_value="000001",
            metric="pe", operator="<", threshold=15.0, channel="web",
        )
        triggered = AlertEngine.evaluate_rule(empty_duckdb_conn, rule)
        assert len(triggered) == 1

    def test_evaluate_change_pct(self, empty_duckdb_conn):
        from alerts.engine import AlertRule, AlertEngine
        _seed_basic_kline(empty_duckdb_conn, stock_code="000001", change_pct=8.5)

        rule = AlertRule.create(
            conn=empty_duckdb_conn, rule_id="r3", name="涨>5%",
            target_type="stock", target_value="000001",
            metric="change_pct", operator=">", threshold=5.0, channel="web",
        )
        triggered = AlertEngine.evaluate_rule(empty_duckdb_conn, rule)
        assert len(triggered) == 1
        assert triggered[0].value == 8.5

    def test_evaluate_stock_not_in_db(self, empty_duckdb_conn):
        from alerts.engine import AlertRule, AlertEngine
        rule = AlertRule.create(
            conn=empty_duckdb_conn, rule_id="r1", name="x",
            target_type="stock", target_value="999999",
            metric="pe", operator=">", threshold=10.0, channel="web",
        )
        triggered = AlertEngine.evaluate_rule(empty_duckdb_conn, rule)
        assert triggered == []


class TestAlertRunAll:
    def test_run_all_creates_events(self, empty_duckdb_conn):
        from alerts.engine import AlertRule, AlertEngine

        _seed_basic_finance(empty_duckdb_conn, stock_code="000001", pe=80.0)
        _seed_basic_finance(empty_duckdb_conn, stock_code="000002", pe=20.0)

        AlertRule.create(
            conn=empty_duckdb_conn, rule_id="r1", name="PE>50",
            target_type="stock", target_value="000001",
            metric="pe", operator=">", threshold=50.0, channel="web",
        )
        AlertRule.create(
            conn=empty_duckdb_conn, rule_id="r2", name="PE<15",
            target_type="stock", target_value="000002",
            metric="pe", operator="<", threshold=15.0, channel="web",
        )
        AlertRule.create(
            conn=empty_duckdb_conn, rule_id="r3", name="PE>100",
            target_type="stock", target_value="000001",
            metric="pe", operator=">", threshold=100.0, channel="web",
        )

        events = AlertEngine.run_all(empty_duckdb_conn)
        rule_ids = {ev.rule_id for ev in events}
        assert "r1" in rule_ids
        assert "r2" not in rule_ids
        assert "r3" not in rule_ids

        n = empty_duckdb_conn.execute("SELECT COUNT(*) FROM alert_event").fetchone()[0]
        assert n == len(events)
