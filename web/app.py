"""
FastAPI 应用工厂。

- create_app() 返回装配好所有路由的 FastAPI 实例
- 所有 v1.1 新增路由都在 /api/v1/* 下
"""
from __future__ import annotations
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from web import db as web_db

import pypinyin

# ---------- 股票搜索（拼音首字母 / 中文 / 代码 模糊联想） ----------
_STOCK_INDEX = None


def _build_stock_index():
    conn = web_db.get_conn()
    rows = conn.execute(
        "SELECT stock_code, short_name FROM stock_basic ORDER BY stock_code"
    ).fetchall()
    idx = []
    for code, name in rows:
        py = "".join(pypinyin.lazy_pinyin(name))
        ini = "".join(pypinyin.lazy_pinyin(name, style=pypinyin.Style.FIRST_LETTER))
        idx.append({"stock_code": code, "short_name": name, "pinyin": py, "initials": ini})
    return idx


def get_stock_index():
    global _STOCK_INDEX
    if _STOCK_INDEX is None:
        _STOCK_INDEX = _build_stock_index()
    return _STOCK_INDEX


def _score_stock(q: str, s: dict) -> int:
    q = (q or "").strip().lower()
    if not q:
        return 0
    code = s["stock_code"].lower()
    name = s["short_name"]
    py = s["pinyin"]
    ini = s["initials"]
    if q == code:
        return 100
    if code.startswith(q):
        return 92
    if q in code:
        return 72
    if name == q:
        return 96
    if q in name:
        return 82
    if ini == q:
        return 90
    if ini.startswith(q):
        return 85
    if q in ini:
        return 70
    if q in py:
        return 60
    return 0


def match_stock_codes(q: str, limit: int | None = None) -> list[str]:
    """按 拼音首字母 / 全拼 / 中文 / 代码 模糊匹配，返回命中的股票代码（按相关度排序）。"""
    q = (q or "").strip()
    if not q:
        return []
    idx = get_stock_index()
    scored = [(_score_stock(q, s), s["stock_code"]) for s in idx]
    scored = [(sc, c) for sc, c in scored if sc > 0]
    scored.sort(key=lambda x: (-x[0], x[1]))
    codes = [c for _, c in scored]
    return codes[:limit] if limit else codes


def create_app() -> FastAPI:
    app = FastAPI(title="Quant Workbench", version="1.1")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        allow_headers=["*"],
    )

    STATIC_DIR = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": app.version}

    # ---------- 数据健康度 ----------
    @app.get("/api/stats")
    def stats():
        sql = """
                SELECT 'stock_basic' AS tbl, COUNT(*) AS rows,
                       COUNT(DISTINCT stock_code) AS stocks_or_keys
                FROM stock_basic
                UNION ALL SELECT 'daily_kline', COUNT(*), COUNT(DISTINCT stock_code)
                FROM daily_kline WHERE k_type=1
                UNION ALL SELECT 'minute_kline', COUNT(*), COUNT(DISTINCT stock_code)
                FROM minute_kline WHERE k_type=5
                UNION ALL SELECT 'index_kline', COUNT(*), COUNT(DISTINCT index_code)
                FROM index_kline
                UNION ALL SELECT 'factors_daily', COUNT(*), COUNT(DISTINCT stock_code)
                FROM factors_daily
                UNION ALL SELECT 'trade_calendar', COUNT(*), NULL
                FROM trade_calendar
                UNION ALL SELECT 'update_log', COUNT(*), NULL
                FROM update_log
            """
        try:
            r = web_db.query_dict(sql, limit=100)
            conn = web_db.get_conn()
            latest = conn.execute(
                "SELECT MAX(trade_date) FROM daily_kline WHERE k_type=1"
            ).fetchone()[0]
            exch_rows = conn.execute(
                "SELECT exchange, COUNT(*) FROM stock_basic GROUP BY exchange"
            ).fetchall()
            exchanges = {e: n for e, n in exch_rows}
            return {
                "tables": r.get("rows", []),
                "latest_trade_date": str(latest),
                "exchanges": exchanges,
            }
        except Exception as e:
            return {"error": str(e), "tables": [], "latest_trade_date": None, "exchanges": {}}

    # ---------- 全市场快照 ----------
    @app.get("/api/snapshot")
    def snapshot(
        exchange: str | None = Query(None, pattern="^(SH|SZ|BJ)$"),
        sort: str = Query("change_pct", pattern="^(change_pct|amount|volume|close)$"),
        order: str = Query("desc", pattern="^(asc|desc)$"),
        limit: int = Query(50, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        q: str | None = Query(None),
    ):
        latest = web_db.get_latest_trade_date()
        where = ["a.k_type=1", "a.trade_date = ?", "a.volume > 0"]
        params: list = [latest]
        if exchange:
            where.append("b.exchange = ?")
            params.append(exchange)
        if q:
            # 支持 拼音首字母 / 全拼 / 中文 / 代码 的模糊匹配（全市场）
            codes = match_stock_codes(q)
            if codes:
                where.append(f"b.stock_code IN ({','.join(['?'] * len(codes))})")
                params.extend(codes)
            else:
                where.append("(b.short_name LIKE ? OR b.stock_code LIKE ?)")
                params.extend([f"%{q}%", f"%{q}%"])
        where_sql = " AND ".join(where)
        sort_col = {
            "change_pct": "a.change_pct", "amount": "a.amount",
            "volume": "a.volume", "close": "a.close",
        }[sort]
        sql = f"""
            SELECT a.stock_code AS code, b.short_name AS name, b.exchange AS exchange,
                   a.close, a.change, ROUND(a.change_pct, 2) AS change_pct,
                   a.volume, a.amount
            FROM daily_kline a JOIN stock_basic b USING (stock_code)
            WHERE {where_sql}
            ORDER BY {sort_col} {order.upper()}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        r = web_db.query_dict(sql, params=params, limit=limit + 1)
        if "error" in r:
            return r
        total_params = params[:-2]
        total = web_db.get_conn().execute(
            f"SELECT COUNT(*) FROM daily_kline a JOIN stock_basic b USING (stock_code) WHERE {where_sql}",
            total_params,
        ).fetchone()[0]
        return {
            "trade_date": latest,
            "rows": r["rows"][:limit],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    # ---------- 个股搜索 + 基础信息 ----------
    @app.get("/api/stock/info")
    def stock_info(q: str = Query(..., min_length=1)):
        conn = web_db.get_conn()
        row = conn.execute(
            "SELECT stock_code, short_name, exchange, list_date FROM stock_basic "
            "WHERE stock_code = ? OR short_name LIKE ? "
            "ORDER BY CASE WHEN stock_code=? THEN 0 WHEN short_name LIKE ? THEN 1 ELSE 2 END LIMIT 1",
            [q, f"%{q}%", q, f"{q}%"],
        ).fetchone()
        if not row:
            # 回退：用搜索索引做拼音首字母 / 模糊解析
            hits = stock_search(q=q, limit=1).get("results", [])
            if hits:
                row = conn.execute(
                    "SELECT stock_code, short_name, exchange, list_date FROM stock_basic "
                    "WHERE stock_code = ?", [hits[0]["stock_code"]],
                ).fetchone()
        if not row:
            return {"error": f"未找到股票 {q}"}
        code = row[0]
        latest = web_db.get_latest_trade_date()
        lr = conn.execute(
            "SELECT close, change, ROUND(change_pct,2), volume, amount "
            "FROM daily_kline WHERE stock_code=? AND k_type=1 AND trade_date=?",
            [code, latest],
        ).fetchone()
        return {
            "stock_code": row[0], "short_name": row[1], "exchange": row[2],
            "list_date": str(row[3]) if row[3] else None,
            "last_close": lr[0] if lr else None,
            "last_pct": lr[2] if lr else None,
            "last_date": str(latest),
        }

    # ---------- 个股模糊联想（拼音首字母 / 中文 / 代码） ----------
    @app.get("/api/stock/search")
    def stock_search(q: str = Query("", min_length=0), limit: int = Query(20, le=50)):
        q = (q or "").strip()
        idx = get_stock_index()
        if not q:
            return {"results": idx[:limit]}
        scored = [( _score_stock(q, s), s) for s in idx]
        scored = [(sc, s) for sc, s in scored if sc > 0]
        scored.sort(key=lambda x: (-x[0], x[1]["stock_code"]))
        return {"results": [s for _, s in scored[:limit]]}

    # ---------- 个股日 K（图表用） ----------
    @app.get("/api/stock/kline")
    def stock_kline(code: str = Query(...), days: int = Query(120, le=2000)):
        latest = web_db.get_latest_trade_date()
        start = str(web_db.get_conn().execute(
            "SELECT MIN(trade_date) FROM (SELECT trade_date FROM daily_kline "
            "WHERE stock_code=? AND k_type=1 AND trade_date <= ? ORDER BY trade_date DESC LIMIT ?)",
            [code, latest, days],
        ).fetchone()[0])
        sql = """
            SELECT trade_date, open, high, low, close, pre_close, change, change_pct,
                   volume, amount
            FROM daily_kline
            WHERE stock_code=? AND k_type=1 AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date
        """
        return web_db.query_dict(sql, params=[code, start, latest], limit=days)

    # ---------- 个股周 K（k_type=2，由日 K 聚合） ----------
    @app.get("/api/stock/weekly")
    def stock_weekly(code: str = Query(...), weeks: int = Query(120, le=2000)):
        latest = web_db.get_latest_trade_date()
        start = str(web_db.get_conn().execute(
            "SELECT MIN(trade_date) FROM (SELECT trade_date FROM daily_kline "
            "WHERE stock_code=? AND k_type=2 AND trade_date <= ? ORDER BY trade_date DESC LIMIT ?)",
            [code, latest, weeks],
        ).fetchone()[0])
        sql = """
            SELECT trade_date, open, high, low, close, pre_close, change, change_pct,
                   volume, amount
            FROM daily_kline
            WHERE stock_code=? AND k_type=2 AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date
        """
        return web_db.query_dict(sql, params=[code, start, latest], limit=weeks)

    # ---------- 个股分时（5 分钟 K 线，默认取最新交易日） ----------
    @app.get("/api/stock/minute")
    def stock_minute(
        code: str = Query(...),
        date: str | None = Query(None),
        days: int = Query(1, ge=1, le=30),
    ):
        conn = web_db.get_conn()
        if not date:
            date = str(conn.execute(
                "SELECT MAX(trade_date) FROM minute_kline "
                "WHERE stock_code=? AND k_type=5", [code],
            ).fetchone()[0])
        # 取最近 days 个有 5min 数据的交易日（默认 1 天=最新一天）
        d0 = str(conn.execute(
            "SELECT MIN(trade_date) FROM (SELECT DISTINCT trade_date FROM minute_kline "
            "WHERE stock_code=? AND k_type=5 AND trade_date<=? "
            "ORDER BY trade_date DESC LIMIT ?)", [code, date, days],
        ).fetchone()[0])
        sql = """
            SELECT trade_date, trade_time, open, high, low, close, volume, amount
            FROM minute_kline
            WHERE stock_code=? AND k_type=5 AND trade_date BETWEEN ? AND ?
            ORDER BY trade_time
        """
        r = web_db.query_dict(sql, params=[code, d0, date], limit=5000)
        if isinstance(r.get("rows"), list):
            for row in r["rows"]:
                row["trade_time"] = str(row["trade_time"])
                row["trade_date"] = str(row["trade_date"])
        return r

    # ---------- 个股 5 分钟 K 可用交易日（降序） ----------
    @app.get("/api/stock/minute/dates")
    def stock_minute_dates(code: str = Query(...), limit: int = Query(60, ge=1, le=500)):
        conn = web_db.get_conn()
        rows = conn.execute(
            "SELECT trade_date, COUNT(*) AS bars FROM minute_kline "
            "WHERE stock_code=? AND k_type=5 GROUP BY trade_date "
            "ORDER BY trade_date DESC LIMIT ?", [code, limit],
        ).fetchall()
        return {"dates": [{"date": str(d), "bars": n} for d, n in rows]}

    # ---------- 指数看板 ----------
    INDEX_NAMES = {
        "sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
        "sh000300": "沪深300", "sh000905": "中证500", "sh000688": "科创50",
    }

    @app.get("/api/indices/board")
    def indices_board():
        conn = web_db.get_conn()
        latest = conn.execute("SELECT MAX(trade_date) FROM index_kline").fetchone()[0]
        rows = conn.execute(
            "SELECT index_code, close, ROUND(change_pct,2) AS change_pct "
            "FROM index_kline WHERE trade_date = ? ORDER BY index_code",
            [latest],
        ).fetchall()
        indices = [
            {"code": r[0], "name": INDEX_NAMES.get(r[0], r[0]), "close": r[1], "change_pct": r[2]}
            for r in rows
        ]
        return {"trade_date": str(latest), "indices": indices}

    @app.get("/api/indices/kline")
    def index_kline(code: str = Query(...), days: int = Query(120, le=5000)):
        conn = web_db.get_conn()
        latest = conn.execute("SELECT MAX(trade_date) FROM index_kline").fetchone()[0]
        start = str(conn.execute(
            "SELECT MIN(trade_date) FROM (SELECT trade_date FROM index_kline "
            "WHERE index_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?)",
            [code, latest, days],
        ).fetchone()[0])
        sql = """
            SELECT trade_date, open, high, low, close, volume, amount, change_pct
            FROM index_kline
            WHERE index_code=? AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date
        """
        return web_db.query_dict(sql, params=[code, start, latest], limit=days)

    # ---------- 指数分时（5 分钟 K 线，默认取最新交易日） ----------
    @app.get("/api/indices/minute")
    def index_minute(
        code: str = Query(...),
        date: str | None = Query(None),
        days: int = Query(1, ge=1, le=30),
    ):
        conn = web_db.get_conn()
        if not date:
            date = str(conn.execute(
                "SELECT MAX(trade_date) FROM index_minute_kline "
                "WHERE index_code=? AND k_type=5", [code],
            ).fetchone()[0])
        d0 = str(conn.execute(
            "SELECT MIN(trade_date) FROM (SELECT DISTINCT trade_date FROM index_minute_kline "
            "WHERE index_code=? AND k_type=5 AND trade_date<=? "
            "ORDER BY trade_date DESC LIMIT ?)", [code, date, days],
        ).fetchone()[0])
        sql = """
            SELECT trade_date, trade_time, open, high, low, close, volume, amount
            FROM index_minute_kline
            WHERE index_code=? AND k_type=5 AND trade_date BETWEEN ? AND ?
            ORDER BY trade_time
        """
        r = web_db.query_dict(sql, params=[code, d0, date], limit=5000)
        if isinstance(r.get("rows"), list):
            for row in r["rows"]:
                row["trade_time"] = str(row["trade_time"])
                row["trade_date"] = str(row["trade_date"])
        return r

    # ---------- 指数 5 分钟 K 可用交易日（降序） ----------
    @app.get("/api/indices/minute/dates")
    def index_minute_dates(code: str = Query(...), limit: int = Query(60, ge=1, le=500)):
        conn = web_db.get_conn()
        rows = conn.execute(
            "SELECT trade_date, COUNT(*) AS bars FROM index_minute_kline "
            "WHERE index_code=? AND k_type=5 GROUP BY trade_date "
            "ORDER BY trade_date DESC LIMIT ?", [code, limit],
        ).fetchall()
        return {"dates": [{"date": str(d), "bars": n} for d, n in rows]}

    # ---------- SQL 面板（只读） ----------
    @app.post("/api/sql/query")
    def sql_query(payload: dict = Body(...)):
        sql = (payload or {}).get("query", "").strip()
        if not sql:
            return {"error": "请输入 SQL"}
        ok, err = web_db.safe_sql_check(sql)
        if not ok:
            return {"error": err}
        return web_db.query_dict(sql, limit=5000, timeout_ms=15000)

    # 注册 v1.1 新增路由（用单独模块，避免 app.py 过长）
    from web.routes import register_v1_1_routes
    register_v1_1_routes(app)

    # 注册原有 ai_routes（在 web/ai_routes.py 里）
    try:
        from web import ai_routes
        app.include_router(ai_routes.router)
    except Exception as e:
        print(f"[warn] ai_routes 注册失败: {e}")

    return app


# 兼容老启动方式（web.server / python -m web.server）
app = create_app()


if __name__ == "__main__":
    import uvicorn
    print("🚀 Quant Workbench Web 启动中...")
    print("📊 访问 http://localhost:8765  （Ctrl+C 停止）")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
