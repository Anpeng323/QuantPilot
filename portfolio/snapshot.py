"""
持仓快照：基于 position 表 + 最新价格（DB 或外部注入）计算市值和盈亏。
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict as _asdict
from typing import Dict, Optional, List, Any
import duckdb


@dataclass
class PositionSnapshot:
    """单条持仓的快照"""
    position_id: str
    stock_code: str
    short_name: str = ""
    cost_price: float = 0.0
    quantity: int = 0
    cost_total: float = 0.0
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    floating_pnl: Optional[float] = None
    return_pct: Optional[float] = None
    open_date: str = ""
    note: str = ""
    status: str = "open"

    def to_dict(self) -> Dict[str, Any]:
        return _asdict(self)


class PortfolioSnapshot:
    """
    持仓快照计算器。
    - prices 参数：可选，{stock_code: price} 覆盖 DB 中的最新价
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection, prices: Optional[Dict[str, float]] = None):
        self.conn = conn
        self.prices = prices or {}

    def _latest_prices_from_kline(self) -> Dict[str, float]:
        """从 daily_kline 取最新一日收盘价"""
        result: dict = {}
        rows = self.conn.execute("""
            SELECT stock_code, close FROM daily_kline
            WHERE k_type=1 AND trade_date = (
                SELECT MAX(trade_date) FROM daily_kline
            )
        """).fetchall()
        for stock_code, close in rows:
            result[str(stock_code)] = float(close)
        return result

    def _latest_prices_per_stock(self) -> Dict[str, float]:
        """每只票各自的最新一日收盘价（更精确但 N 次查询）"""
        result: dict = {}
        stocks = self.conn.execute(
            "SELECT DISTINCT stock_code FROM position WHERE status='open'"
        ).fetchall()
        for (stock_code,) in stocks:
            row = self.conn.execute("""
                SELECT close FROM daily_kline
                WHERE stock_code=? AND k_type=1
                ORDER BY trade_date DESC LIMIT 1
            """, [stock_code]).fetchone()
            if row:
                result[str(stock_code)] = float(row[0])
        return result

    def compute(self) -> List[PositionSnapshot]:
        """计算所有 open 持仓的快照"""
        # 先看外部注入的价格，没有再查 DB
        external = self.prices
        # 仍然查询每只票各自的最新价，方便对比
        kline_prices = self._latest_prices_per_stock()

        # 合并：外部覆盖
        merged = dict(kline_prices)
        for k, v in external.items():
            if v is not None:
                merged[str(k)] = float(v)

        # 查 open 持仓
        rows = self.conn.execute("""
            SELECT p.position_id, p.stock_code, COALESCE(s.short_name, ''),
                   p.cost_price, p.quantity, p.cost_total,
                   COALESCE(CAST(p.open_date AS VARCHAR), ''),
                   COALESCE(p.note, ''), p.status
            FROM position p
            LEFT JOIN stock_basic s ON p.stock_code = s.stock_code
            WHERE p.status = 'open'
            ORDER BY p.open_date DESC
        """).fetchall()

        snaps: List[PositionSnapshot] = []
        for r in rows:
            pid, code, sname, cost_price, qty, cost_total, open_date, note, status = r
            code = str(code).zfill(6) if code else ""
            cur = merged.get(code)
            snap = PositionSnapshot(
                position_id=str(pid), stock_code=code, short_name=str(sname),
                cost_price=float(cost_price or 0), quantity=int(qty or 0),
                cost_total=float(cost_total or 0),
                open_date=open_date, note=str(note), status=str(status),
            )
            if cur is not None:
                snap.current_price = cur
                snap.market_value = round(cur * snap.quantity, 1)
                snap.floating_pnl = round(snap.market_value - snap.cost_total, 1)
                snap.return_pct = (snap.floating_pnl / snap.cost_total) if snap.cost_total > 0 else None
            snaps.append(snap)
        return snaps

    def total(self) -> Dict[str, Any]:
        """汇总：cost_total / market_value / pnl / return_pct / count"""
        snaps = self.compute()
        cost_total = sum(s.cost_total for s in snaps)
        market_value = sum(s.market_value for s in snaps if s.market_value is not None)
        # 只统计有当前价的部分
        valued = [s for s in snaps if s.floating_pnl is not None]
        floating_pnl = sum(s.floating_pnl for s in valued)
        return {
            "position_count": len(snaps),
            "valued_count": len(valued),
            "cost_total": round(cost_total, 1),
            "market_value": round(market_value, 1),
            "floating_pnl": round(floating_pnl, 1),
            "return_pct": (floating_pnl / cost_total) if cost_total > 0 and market_value > 0 else None,
        }
