"""自选池 CRUD"""
import uuid
import json
from datetime import datetime
from etl.db import get_conn


# ---- group ----
def list_groups() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT group_name, description, sort_order FROM watchlist_group ORDER BY sort_order, group_name"
    ).fetchall()
    return [{"group_name": r[0], "description": r[1], "sort_order": r[2]} for r in rows]


def create_group(group_name: str, description: str = "", sort_order: int = 0) -> dict:
    if not group_name:
        return {"error": "group_name 不能为空"}
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO watchlist_group (group_name, description, sort_order) VALUES (?, ?, ?)",
            [group_name, description, sort_order],
        )
    except Exception as e:
        return {"error": str(e)}
    return {"group_name": group_name, "description": description, "sort_order": sort_order}


def delete_group(group_name: str) -> dict:
    conn = get_conn()
    conn.execute("DELETE FROM watchlist WHERE group_name = ?", [group_name])
    conn.execute("DELETE FROM watchlist_group WHERE group_name = ?", [group_name])
    return {"group_name": group_name, "status": "deleted"}


# ---- items ----
def list_group_items(group_name: str) -> list:
    conn = get_conn()
    rows = conn.execute("""
        SELECT w.stock_code, i.short_name, i.exchange, w.added_at, w.note,
               (SELECT close FROM daily_kline WHERE stock_code=w.stock_code AND k_type=1
                ORDER BY trade_date DESC LIMIT 1) AS last_price,
               (SELECT change_pct FROM daily_kline WHERE stock_code=w.stock_code AND k_type=1
                ORDER BY trade_date DESC LIMIT 1) AS last_pct
        FROM watchlist w
        LEFT JOIN stock_basic i USING (stock_code)
        WHERE w.group_name = ?
        ORDER BY w.added_at DESC
    """, [group_name]).fetchall()
    from etl.industry import enrich_with_board
    items = [{
        "stock_code": r[0], "short_name": r[1] or "", "exchange": r[2] or "",
        "added_at": str(r[3]) if r[3] else "", "note": r[4] or "",
        "last_price": r[5], "last_pct": r[6],
    } for r in rows]
    enrich_with_board(items)
    return items


def add_to_group(group_name: str, stock_code: str, note: str = "") -> dict:
    if not group_name or not stock_code:
        return {"error": "参数缺失"}
    # 校验 group 存在
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM watchlist_group WHERE group_name=?", [group_name]).fetchone():
        return {"error": f"分组 {group_name} 不存在"}
    # 校验 stock 存在
    if not conn.execute("SELECT 1 FROM stock_basic WHERE stock_code=?", [stock_code]).fetchone():
        return {"error": f"股票 {stock_code} 不存在"}
    try:
        conn.execute(
            "INSERT INTO watchlist (group_name, stock_code, added_at, note) VALUES (?, ?, ?, ?)",
            [group_name, stock_code, datetime.now().isoformat(), note],
        )
    except Exception as e:
        return {"error": str(e)}
    return {"group_name": group_name, "stock_code": stock_code, "status": "added"}


def remove_from_group(group_name: str, stock_code: str) -> dict:
    conn = get_conn()
    conn.execute("DELETE FROM watchlist WHERE group_name=? AND stock_code=?",
                 [group_name, stock_code])
    return {"group_name": group_name, "stock_code": stock_code, "status": "removed"}
