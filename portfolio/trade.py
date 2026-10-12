"""交易记录"""
import uuid
from datetime import datetime
from etl.db import get_conn


def list_trades(stock_code: str = "", limit: int = 100) -> list:
    sql = "SELECT trade_id, stock_code, direction, price, quantity, amount, trade_date, logic_text, note_id FROM trade"
    params = []
    if stock_code:
        sql += " WHERE stock_code = ?"
        params.append(stock_code)
    sql += " ORDER BY trade_date DESC LIMIT ?"
    params.append(limit)
    conn = get_conn()
    rows = conn.execute(sql, params).fetchall()
    return [{
        "trade_id": r[0], "stock_code": r[1], "direction": r[2],
        "price": r[3], "quantity": r[4], "amount": r[5],
        "trade_date": str(r[6]) if r[6] else "",
        "logic_text": r[7] or "", "note_id": r[8] or "",
    } for r in rows]


def add_trade(stock_code: str, direction: str, price: float, quantity: int,
              trade_date: str = None, logic_text: str = "",
              note_id: str = "") -> dict:
    if direction not in ("buy", "sell"):
        return {"error": "direction 必须是 buy/sell"}
    if not stock_code or price <= 0 or quantity <= 0:
        return {"error": "参数错误"}
    tid = uuid.uuid4().hex[:12]
    trade_date = trade_date or datetime.now().isoformat()
    amount = price * quantity
    conn = get_conn()
    conn.execute("""
        INSERT INTO trade (trade_id, stock_code, direction, price, quantity, amount, trade_date, logic_text, note_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [tid, stock_code, direction, price, quantity, amount, trade_date, logic_text, note_id])
    return {"trade_id": tid, "stock_code": stock_code, "amount": amount, "status": "added"}
