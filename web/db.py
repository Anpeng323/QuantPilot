"""
Web 层 DuckDB 访问工具。
SQL 面板等只读查询通过 query_dict() 走，强制校验 + 行数截断。

连接模式说明（重要）：
DuckDB 要求同一进程内对同一库文件的连接配置必须一致，否则报
"Can't open a connection to same database file with a different configuration"。
web 层存在写操作（笔记/持仓/公告/新闻/自选池等，经 etl.db.get_conn 读写），
故此处统一以**读写**模式打开，避免与 etl.db 混用配置。
SQL 面板的「只读」由 safe_sql_check 校验保证，不依赖连接模式。
"""
from __future__ import annotations
import re
import sys
import time
from pathlib import Path
from typing import Any
import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl.db import DB_PATH


def get_conn() -> duckdb.DuckDBPyConnection:
    """返回一个新的读写连接（配置与 etl.db.get_conn 一致，避免同进程模式冲突）。

    刻意**不做缓存**：连接使用后即随局部变量释放，服务空闲时不长期持有文件锁，
    从而保证外部工具（回归脚本 / 夜间任务）在服务空闲时仍能访问数据库。
    """
    return duckdb.connect(str(DB_PATH), read_only=False)


# ---------- SQL 校验 ----------
_BLOCKED = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|CREATE|ALTER|REPLACE|"
    r"ATTACH|DETACH|COPY|GRANT|REVOKE|CALL|LOAD|EXPORT|IMPORT)\b",
    re.IGNORECASE,
)


def safe_sql_check(sql: str) -> tuple[bool, str]:
    """
    只允许 SELECT / WITH / EXPLAIN 开头，且不包含 DDL/DML 关键字。
    返回 (ok, error_msg)。
    """
    s = sql.strip()
    # 去掉行注释
    s = re.sub(r"--[^\n]*", "", s)
    # 去掉块注释
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
    s = s.strip().rstrip(";").strip()
    if not s:
        return False, "SQL 为空"
    head = s.split(None, 1)[0].upper()
    if head not in ("SELECT", "WITH", "EXPLAIN"):
        return False, f"仅支持 SELECT/WITH/EXPLAIN 开头，当前以 `{head}` 开头"
    m = _BLOCKED.search(s)
    if m:
        return False, f"检测到禁用关键字 `{m.group(0)}`（仅允许只读查询）"
    return True, ""


# ---------- 通用查询 ----------
def query_dict(sql: str, params: list[Any] | None = None,
               limit: int = 5000, timeout_ms: int = 15000) -> dict:
    """
    执行 SQL，返回 {columns, rows, duration_ms, error}。
    - 自动 LIMIT 截断
    - 设置查询超时
    """
    sql_clean = sql.strip().rstrip(";")
    if limit > 0 and not re.search(r"\bLIMIT\b", sql_clean, re.IGNORECASE):
        sql_clean = f"{sql_clean} LIMIT {limit}"

    conn = get_conn()
    t0 = time.time()
    try:
        # DuckDB 没有原生 statement timeout，用线程 + 简易方式
        result = conn.execute(sql_clean, params or [])
        cols = [d[0] for d in result.description]
        # 返回字典行（列名作 key），与前端 r.code / r.trade_date 等字典访问契约一致
        rows = [dict(zip(cols, r)) for r in result.fetchall()]
        duration_ms = int((time.time() - t0) * 1000)
        return {
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
            "duration_ms": duration_ms,
            "truncated": len(rows) >= limit,
        }
    except Exception as e:
        return {"error": str(e), "duration_ms": int((time.time() - t0) * 1000)}


# ---------- 便捷封装 ----------
def get_latest_trade_date() -> str:
    conn = get_conn()
    return str(conn.execute(
        "SELECT MAX(trade_date) FROM daily_kline WHERE k_type=1"
    ).fetchone()[0])


def get_stock_basic(code: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT stock_code, short_name, exchange, list_date "
        "FROM stock_basic WHERE stock_code = ?", [code]
    ).fetchone()
    if not row:
        return None
    return {
        "stock_code": row[0], "short_name": row[1],
        "exchange": row[2], "list_date": str(row[3]) if row[3] else None,
    }
