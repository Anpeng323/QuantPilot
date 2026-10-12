"""持仓台账"""
import uuid
from datetime import datetime, date
from etl.db import get_conn


def list_positions(status: str = "open") -> list:
    sql = """SELECT p.position_id, p.stock_code, b.short_name,
                  p.cost_price, p.quantity, p.cost_total,
                  p.open_date, p.note, p.status,
                  (SELECT close FROM daily_kline WHERE stock_code=p.stock_code AND k_type=1
                   ORDER BY trade_date DESC LIMIT 1) AS last_price,
                  (SELECT change_pct FROM daily_kline WHERE stock_code=p.stock_code AND k_type=1
                   ORDER BY trade_date DESC LIMIT 1) AS last_pct
           FROM position p
           LEFT JOIN stock_basic b USING (stock_code)
           WHERE p.status = ? OR ? = 'all'
           ORDER BY p.open_date DESC"""
    conn = get_conn()
    rows = conn.execute(sql, [status, status]).fetchall()
    return [_fmt_position(r) for r in rows]


def _fmt_position(r) -> dict:
    cost = float(r[5] or 0)
    last = float(r[9] or 0)
    qty = int(r[4] or 0)
    market_value = last * qty
    pnl = market_value - cost
    pnl_pct = (pnl / cost * 100) if cost > 0 else 0
    return {
        "position_id": r[0], "stock_code": r[1], "short_name": r[2] or "",
        "cost_price": float(r[3] or 0), "quantity": qty, "cost_total": cost,
        "open_date": str(r[6]) if r[6] else "", "note": r[7] or "", "status": r[8] or "open",
        "last_price": last, "last_pct": float(r[10]) if r[10] is not None else None,
        "market_value": round(market_value, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
    }


def add_position(stock_code: str, cost_price: float, quantity: int,
                 open_date: str = None, note: str = "") -> dict:
    if not stock_code or cost_price <= 0 or quantity <= 0:
        return {"error": "参数错误"}
    pid = uuid.uuid4().hex[:12]
    open_date = open_date or date.today().isoformat()
    cost_total = cost_price * quantity
    conn = get_conn()
    conn.execute("""
        INSERT INTO position (position_id, stock_code, cost_price, quantity, cost_total, open_date, note, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
    """, [pid, stock_code, cost_price, quantity, cost_total, open_date, note])
    return {"position_id": pid, "stock_code": stock_code, "status": "added",
            "cost_total": cost_total}


def close_position(position_id: str) -> dict:
    conn = get_conn()
    conn.execute("UPDATE position SET status='closed' WHERE position_id=?", [position_id])
    return {"position_id": position_id, "status": "closed"}


# ============== v1.1 增量：类式 API（与函数式并存，向后兼容） ==============
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class Position:
    """持仓（类式 API）"""
    position_id: str
    stock_code: str
    cost_price: float
    quantity: int
    cost_total: float
    open_date: str
    note: str = ""
    status: str = "open"
    short_name: str = ""

    @staticmethod
    def create(
        conn,
        position_id: Optional[str] = None,
        stock_code: str = "",
        cost_price: float = 0.0,
        quantity: int = 0,
        cost_total: float = 0.0,
        open_date: str = "",
        note: str = "",
        status: str = "open",
    ) -> "Position":
        import uuid as _uuid
        pid = position_id or _uuid.uuid4().hex[:12]
        existing = conn.execute("SELECT 1 FROM position WHERE position_id=?", [pid]).fetchone()
        if existing:
            raise ValueError(f"持仓 id 已存在: {pid}")
        if not cost_total:
            cost_total = cost_price * quantity
        conn.execute("""
            INSERT INTO position (position_id, stock_code, cost_price, quantity,
                                   cost_total, open_date, note, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [pid, stock_code, cost_price, quantity, cost_total, open_date, note, status])
        return Position.get(conn, pid)

    @staticmethod
    def get(conn, position_id: str) -> Optional["Position"]:
        row = conn.execute("""
            SELECT p.position_id, p.stock_code, COALESCE(s.short_name, ''),
                   p.cost_price, p.quantity, p.cost_total,
                   COALESCE(CAST(p.open_date AS VARCHAR), ''),
                   COALESCE(p.note, ''), p.status
            FROM position p LEFT JOIN stock_basic s USING (stock_code)
            WHERE p.position_id=?
        """, [position_id]).fetchone()
        if not row:
            return None
        return Position(
            position_id=row[0], stock_code=row[1], short_name=row[2] or "",
            cost_price=float(row[3] or 0), quantity=int(row[4] or 0),
            cost_total=float(row[5] or 0), open_date=row[6] or "",
            note=row[7] or "", status=row[8] or "open",
        )

    @staticmethod
    def list_open(conn) -> List["Position"]:
        rows = conn.execute("SELECT position_id FROM position WHERE status='open'").fetchall()
        return [Position.get(conn, r[0]) for r in rows if Position.get(conn, r[0])]

    @staticmethod
    def close(conn, position_id: str) -> bool:
        cur = conn.execute("UPDATE position SET status='closed' WHERE position_id=?", [position_id])
        return cur.rowcount > 0
