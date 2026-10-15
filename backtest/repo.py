"""
回测结果持久化（Phase 1 / T7）。

表结构见 etl/schema.sql（backtest_task / backtest_result / strategy_record）。
模块加载时确保表存在（CREATE TABLE IF NOT EXISTS），无需重新跑 init_schema。
"""
from __future__ import annotations
import json
import uuid

from etl.db import get_conn

_DDL = """
CREATE TABLE IF NOT EXISTS backtest_task (
    task_id       VARCHAR PRIMARY KEY,
    stock_code    VARCHAR(6),
    start_date    DATE,
    end_date      DATE,
    signal        VARCHAR(256),
    initial_cash  DOUBLE,
    fill_price    VARCHAR(8),
    benchmark     VARCHAR(16),
    created_at    TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS backtest_result (
    task_id       VARCHAR PRIMARY KEY,
    total_return  DOUBLE,
    annual_return DOUBLE,
    max_drawdown  DOUBLE,
    sharpe        DOUBLE,
    win_rate      DOUBLE,
    excess_return DOUBLE,
    trade_count   INT,
    data_flag     VARCHAR(64),
    payload       VARCHAR,
    created_at    TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS strategy_record (
    strategy_id   VARCHAR PRIMARY KEY,
    name          VARCHAR(128),
    source        VARCHAR(16),
    definition    VARCHAR,
    code_hash     VARCHAR(64),
    metrics_json  VARCHAR,
    created_at    TIMESTAMP DEFAULT current_timestamp
);
"""

_ensured = False


def _ensure():
    global _ensured
    if _ensured:
        return
    conn = get_conn()
    try:
        conn.execute(_DDL)
    finally:
        conn.close()
    _ensured = True


def save_task(req, result: dict) -> str:
    """保存一次回测任务与结果，返回 task_id。"""
    _ensure()
    task_id = "bt_" + uuid.uuid4().hex[:12]
    m = result["metrics"]
    payload = json.dumps({
        "trades": result["trades"],
        "equity_curve": result["equity_curve"],
        "metrics": m,
        "data_flag": result["data_flag"],
    }, ensure_ascii=False)
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO backtest_task"
            "(task_id, stock_code, start_date, end_date, signal, initial_cash, fill_price, benchmark) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [task_id, req.stock_code, req.start, req.end, req.signal,
             req.initial_cash, req.fill_price, req.benchmark],
        )
        conn.execute(
            "INSERT INTO backtest_result"
            "(task_id, total_return, annual_return, max_drawdown, sharpe, win_rate, "
             "excess_return, trade_count, data_flag, payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [task_id, m["total_return"], m["annual_return"], m["max_drawdown"], m["sharpe"],
             m["win_rate"], m["excess_return"], len(result["trades"]),
             result["data_flag"], payload],
        )
    finally:
        conn.close()
    return task_id


def list_tasks(limit: int = 20) -> list[dict]:
    _ensure()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT t.task_id, t.stock_code, t.signal, t.start_date, t.end_date, "
            "       r.total_return, r.trade_count, t.created_at "
            "FROM backtest_task t LEFT JOIN backtest_result r ON t.task_id = r.task_id "
            "ORDER BY t.created_at DESC LIMIT ?",
            [limit],
        ).fetchall()
    finally:
        conn.close()
    cols = ["task_id", "stock_code", "signal", "start_date", "end_date",
            "total_return", "trade_count", "created_at"]
    out = []
    for r in rows:
        d = {}
        for i, col in enumerate(cols):
            val = r[i]
            d[col] = str(val) if col == "created_at" else val
        out.append(d)
    return out


def get_task(task_id: str) -> dict | None:
    _ensure()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT t.task_id, t.stock_code, t.signal, t.start_date, t.end_date, "
            "       t.initial_cash, t.fill_price, t.benchmark, t.created_at, r.payload "
            "FROM backtest_task t JOIN backtest_result r ON t.task_id = r.task_id "
            "WHERE t.task_id = ?",
            [task_id],
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    (tid, code, sig, sd, ed, cash, fp, bench, created, payload) = row
    data = json.loads(payload)
    return {
        "task_id": tid, "stock_code": code, "signal": sig,
        "start": str(sd), "end": str(ed), "initial_cash": cash,
        "fill_price": fp, "benchmark": bench, "created_at": str(created),
        "trades": data["trades"], "equity_curve": data["equity_curve"],
        "metrics": data["metrics"], "data_flag": data["data_flag"],
    }


def save_strategy_record(name: str, source: str, definition: str,
                         code_hash: str, metrics: dict) -> tuple[str, bool]:
    """保存一条策略记录（NL 或规则）。按 code_hash 去重：若已存在返回 (已有id, True)。

    返回 (strategy_id, duplicate)。
    """
    _ensure()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT strategy_id FROM strategy_record WHERE code_hash = ?", [code_hash]
        ).fetchone()
        if row:
            return row[0], True
        sid = "st_" + uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO strategy_record"
            "(strategy_id, name, source, definition, code_hash, metrics_json) "
            "VALUES (?,?,?,?,?,?)",
            [sid, name, source, definition, code_hash,
             json.dumps(metrics, ensure_ascii=False)],
        )
    finally:
        conn.close()
    return sid, False


def list_strategies(limit: int = 50) -> list[dict]:
    _ensure()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT strategy_id, name, source, code_hash, created_at "
            "FROM strategy_record ORDER BY created_at DESC LIMIT ?",
            [limit],
        ).fetchall()
    finally:
        conn.close()
    cols = ["strategy_id", "name", "source", "code_hash", "created_at"]
    return [
        {c: (str(r[i]) if c == "created_at" else r[i]) for i, c in enumerate(cols)}
        for r in rows
    ]


def get_strategy(strategy_id: str) -> dict | None:
    _ensure()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT strategy_id, name, source, definition, code_hash, metrics_json, created_at "
            "FROM strategy_record WHERE strategy_id = ?",
            [strategy_id],
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    (sid, name, source, definition, code_hash, metrics_json, created) = row
    return {
        "strategy_id": sid, "name": name, "source": source,
        "definition": definition, "code_hash": code_hash,
        "metrics": json.loads(metrics_json) if metrics_json else None,
        "created_at": str(created),
    }
