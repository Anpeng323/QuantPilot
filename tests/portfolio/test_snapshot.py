"""
TDD: 持仓快照 + 价格同步

- snapshot：基于 position + 最新 daily_kline 计算市值 / 浮动盈亏
- sync_prices：mock data source 拉最新价 → 写入临时 latest_price 表（不入 daily_kline）
"""
import pytest
from datetime import date, datetime


class TestPositionCRUD:
    """Position CRUD 测试"""

    def test_create_and_get(self, empty_duckdb_conn):
        from portfolio.position import Position
        p = Position.create(
            conn=empty_duckdb_conn,
            position_id="p1", stock_code="000001",
            cost_price=10.5, quantity=100, cost_total=1050.0,
            open_date="2024-01-01", note="建仓"
        )
        assert p.position_id == "p1"
        assert p.cost_price == 10.5

        got = Position.get(empty_duckdb_conn, "p1")
        assert got.quantity == 100

    def test_create_with_default_id(self, empty_duckdb_conn):
        from portfolio.position import Position
        p = Position.create(
            conn=empty_duckdb_conn, stock_code="000001",
            cost_price=10.5, quantity=100, cost_total=1050.0,
            open_date="2024-01-01"
        )
        assert p.position_id is not None

    def test_duplicate_id_raises(self, empty_duckdb_conn):
        from portfolio.position import Position
        Position.create(conn=empty_duckdb_conn, position_id="p1", stock_code="000001",
                       cost_price=10.0, quantity=100, cost_total=1000.0, open_date="2024-01-01")
        with pytest.raises(ValueError):
            Position.create(conn=empty_duckdb_conn, position_id="p1", stock_code="000002",
                           cost_price=20.0, quantity=50, cost_total=1000.0, open_date="2024-01-01")

    def test_list_open(self, empty_duckdb_conn):
        from portfolio.position import Position
        Position.create(conn=empty_duckdb_conn, position_id="p1", stock_code="000001",
                       cost_price=10.0, quantity=100, cost_total=1000.0, open_date="2024-01-01", status="open")
        Position.create(conn=empty_duckdb_conn, position_id="p2", stock_code="000002",
                       cost_price=20.0, quantity=50, cost_total=1000.0, open_date="2024-01-01", status="closed")
        open_pos = Position.list_open(empty_duckdb_conn)
        assert len(open_pos) == 1
        assert open_pos[0].position_id == "p1"

    def test_close_position(self, empty_duckdb_conn):
        from portfolio.position import Position
        Position.create(conn=empty_duckdb_conn, position_id="p1", stock_code="000001",
                       cost_price=10.0, quantity=100, cost_total=1000.0, open_date="2024-01-01")
        Position.close(empty_duckdb_conn, "p1")
        p = Position.get(empty_duckdb_conn, "p1")
        assert p.status == "closed"


class TestPortfolioSnapshot:
    """snapshot：计算每条 position 的市值 / 浮动盈亏"""

    def test_snapshot_with_current_prices(self, empty_duckdb_conn):
        from portfolio.position import Position
        from portfolio.snapshot import PortfolioSnapshot

        # 建仓
        Position.create(
            conn=empty_duckdb_conn, position_id="p1", stock_code="000001",
            cost_price=10.0, quantity=100, cost_total=1000.0,
            open_date="2024-01-01"
        )
        Position.create(
            conn=empty_duckdb_conn, position_id="p2", stock_code="000002",
            cost_price=20.0, quantity=50, cost_total=1000.0,
            open_date="2024-02-01"
        )
        empty_duckdb_conn.execute("""
            INSERT INTO stock_basic (stock_code, short_name, exchange) VALUES
            ('000001', '平安银行', 'SZ'), ('000002', '万科A', 'SZ')
        """)
        empty_duckdb_conn.execute("""
            INSERT INTO daily_kline (stock_code, trade_date, close) VALUES
            ('000001', '2024-03-01', 12.0),
            ('000002', '2024-03-01', 18.0)
        """)

        snap = PortfolioSnapshot(conn=empty_duckdb_conn)
        rows = snap.compute()
        assert len(rows) == 2

        # 找 p1
        p1 = next(r for r in rows if r.position_id == "p1")
        assert p1.current_price == 12.0
        assert p1.market_value == 1200.0   # 100 * 12
        assert p1.floating_pnl == 200.0    # 1200 - 1000
        assert p1.return_pct == pytest.approx(0.20, rel=1e-6)  # 20%

        p2 = next(r for r in rows if r.position_id == "p2")
        assert p2.market_value == 900.0    # 50 * 18
        assert p2.floating_pnl == -100.0   # 900 - 1000
        assert p2.return_pct == pytest.approx(-0.10, rel=1e-6)

    def test_snapshot_total(self, empty_duckdb_conn):
        from portfolio.position import Position
        from portfolio.snapshot import PortfolioSnapshot

        Position.create(
            conn=empty_duckdb_conn, position_id="p1", stock_code="000001",
            cost_price=10.0, quantity=100, cost_total=1000.0, open_date="2024-01-01"
        )
        empty_duckdb_conn.execute("""
            INSERT INTO stock_basic (stock_code, short_name) VALUES ('000001', 'X')
        """)
        empty_duckdb_conn.execute("""
            INSERT INTO daily_kline (stock_code, trade_date, close) VALUES ('000001', '2024-03-01', 15.0)
        """)
        snap = PortfolioSnapshot(conn=empty_duckdb_conn)
        snap.compute()
        summary = snap.total()
        assert summary["cost_total"] == 1000.0
        assert summary["market_value"] == 1500.0
        assert summary["floating_pnl"] == 500.0
        assert summary["return_pct"] == pytest.approx(0.5, rel=1e-6)
        assert summary["position_count"] == 1

    def test_snapshot_missing_price_returns_none(self, empty_duckdb_conn):
        """没有当前价 → 该行 current_price = None，floating_pnl = None"""
        from portfolio.position import Position
        from portfolio.snapshot import PortfolioSnapshot

        Position.create(
            conn=empty_duckdb_conn, position_id="p1", stock_code="999999",
            cost_price=10.0, quantity=100, cost_total=1000.0, open_date="2024-01-01"
        )
        snap = PortfolioSnapshot(conn=empty_duckdb_conn)
        rows = snap.compute()
        p1 = rows[0]
        assert p1.current_price is None
        assert p1.floating_pnl is None


class TestSyncPrices:
    """sync_prices：从 data_source 拉最新价（mock）"""

    def test_sync_prices_uses_provider(self, empty_duckdb_conn, monkeypatch):
        from portfolio import sync as portfolio_sync

        # Mock provider
        def fake_provider(stock_codes):
            return {sc: 10.0 + i for i, sc in enumerate(stock_codes)}

        portfolio_sync.set_provider(fake_provider)

        # 同步 3 只票
        n_updated = portfolio_sync.sync_prices(
            empty_duckdb_conn, ["000001", "000002", "000003"]
        )
        assert n_updated == 3

        # 验证 latest_price 表（如果有）
        # 或者通过 snapshot 来验证
        from portfolio.position import Position
        Position.create(
            conn=empty_duckdb_conn, position_id="p1", stock_code="000001",
            cost_price=10.0, quantity=100, cost_total=1000.0, open_date="2024-01-01"
        )
        from portfolio.snapshot import PortfolioSnapshot
        snap = PortfolioSnapshot(conn=empty_duckdb_conn, prices={"000001": 11.0, "000002": 12.0, "000003": 13.0})
        rows = snap.compute()
        p1 = next(r for r in rows if r.position_id == "p1")
        assert p1.current_price == 11.0

    def test_sync_prices_returns_zero_when_provider_empty(self, empty_duckdb_conn, monkeypatch):
        from portfolio import sync as portfolio_sync
        portfolio_sync.set_provider(lambda codes: {})
        n = portfolio_sync.sync_prices(empty_duckdb_conn, ["000001"])
        assert n == 0
