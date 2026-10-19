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
        board_hit: list = []
        if q:
            # 支持 拼音首字母 / 全拼 / 中文 / 代码 / 板块名 的模糊匹配（全市场）
            from etl.industry import match_boards, codes_in_boards
            codes = match_stock_codes(q)
            # 板块名命中（如「银行」「社会服务」）→ 并入该板块全部成分股
            board_hit = match_boards(q)
            merged = list(dict.fromkeys(list(codes) + codes_in_boards(board_hit)))
            if merged:
                where.append(f"b.stock_code IN ({','.join(['?'] * len(merged))})")
                params.extend(merged)
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
        rows = r["rows"][:limit]
        from etl.industry import enrich_with_board
        enrich_with_board(rows, code_key="code")
        return {
            "trade_date": latest,
            "rows": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "boards": board_hit,
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
        from etl.industry import get_board_map
        industry = get_board_map().get(code, "") or ""
        latest = web_db.get_latest_trade_date()
        lr = conn.execute(
            "SELECT close, change, ROUND(change_pct,2), volume, amount "
            "FROM daily_kline WHERE stock_code=? AND k_type=1 AND trade_date=?",
            [code, latest],
        ).fetchone()
        return {
            "stock_code": row[0], "short_name": row[1], "exchange": row[2],
            "list_date": str(row[3]) if row[3] else None,
            "industry": industry,
            "last_close": lr[0] if lr else None,
            "last_pct": lr[2] if lr else None,
            "last_date": str(latest),
        }

    # ---------- 个股模糊联想（拼音首字母 / 中文 / 代码） ----------
    @app.get("/api/stock/search")
    def stock_search(q: str = Query("", min_length=0), limit: int = Query(20, le=50),
                     boards: int = Query(0, ge=0, le=1)):
        q = (q or "").strip()
        idx = get_stock_index()
        from etl.industry import enrich_with_board
        out: dict = {}
        if q and boards:
            # 板块候选（搜索框下拉展示，用于按板块筛选）
            from etl.industry import match_boards, board_counts
            cnt = board_counts()
            hits = match_boards(q, limit=6)
            if hits:
                out["boards"] = [{"name": n, "count": cnt.get(n, 0)} for n in hits]
        if not q:
            out["results"] = idx[:limit]
            return out
        scored = [( _score_stock(q, s), s) for s in idx]
        scored = [(sc, s) for sc, s in scored if sc > 0]
        scored.sort(key=lambda x: (-x[0], x[1]["stock_code"]))
        results = [s for _, s in scored[:limit]]
        enrich_with_board(results, code_key="stock_code")
        out["results"] = results
        return out

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

    # ---------- 个股因子（factors_daily 宽表，按日倒序） ----------
    @app.get("/api/stock/factors")
    def stock_factors(code: str = Query(...), days: int = Query(60, ge=1, le=500)):
        sql = """
            SELECT trade_date, close, mom_5d, mom_10d, mom_20d, mom_60d,
                   vol_20d, vol_60d, volume_ratio_20d, amount_ratio_20d,
                   price_position_20d, bias_ma5, bias_ma20, bias_ma60
            FROM factors_daily
            WHERE stock_code = ?
            ORDER BY trade_date DESC
            LIMIT ?
        """
        out = web_db.query_dict(sql, params=[code, days], limit=days)
        if "error" in out:
            return {"columns": [], "rows": [], "row_count": 0,
                    "message": "暂无因子数据（可运行 analysis/factors.py 生成 factors_daily）"}
        for r in out["rows"]:
            r["trade_date"] = str(r["trade_date"])
        if not out["rows"]:
            out["message"] = "该股票暂无因子数据"
        return out

    # ---------- 个股概览（基础信息 + 最新行情 + 区间统计 + 估值/行业/因子快照） ----------
    @app.get("/api/stock/overview")
    def stock_overview(code: str = Query(...)):
        conn = web_db.get_conn()
        basic = conn.execute(
            "SELECT stock_code, short_name, exchange, list_date FROM stock_basic WHERE stock_code = ?",
            [code],
        ).fetchone()
        if not basic:
            return {"error": f"未找到股票 {code}"}

        def _has_table(tbl: str) -> bool:
            return conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [tbl]
            ).fetchone()[0] > 0

        # 最新行情
        q = conn.execute(
            "SELECT trade_date, open, high, low, close, pre_close, change, change_pct, "
            "       volume, amount, turnover_ratio "
            "FROM daily_kline WHERE stock_code=? AND k_type=1 "
            "ORDER BY trade_date DESC LIMIT 1", [code],
        ).fetchone()
        quote = None
        if q:
            quote = {
                "trade_date": str(q[0]), "open": q[1], "high": q[2], "low": q[3],
                "close": q[4], "pre_close": q[5], "change": q[6],
                "change_pct": round(q[7], 2) if q[7] is not None else None,
                "volume": q[8], "amount": q[9], "turnover_ratio": q[10],
            }

        # 近 250 日区间统计
        st = conn.execute(
            "SELECT COUNT(*), MIN(low), MAX(high), AVG(volume), AVG(amount) FROM ("
            "  SELECT low, high, volume, amount FROM daily_kline "
            "  WHERE stock_code=? AND k_type=1 ORDER BY trade_date DESC LIMIT 250)", [code],
        ).fetchone()
        stats = {"bars": st[0], "low": st[1], "high": st[2],
                 "avg_volume": st[3], "avg_amount": st[4]}

        # 估值 / 财务（finance_core，可能无覆盖）
        valuation = None
        if _has_table("finance_core"):
            fin = conn.execute(
                "SELECT report_date, pe, pb, ps, roe, eps, total_market_cap, "
                "       revenue, net_profit, yoy_revenue, yoy_profit "
                "FROM finance_core WHERE stock_code=? ORDER BY report_date DESC LIMIT 1", [code],
            ).fetchone()
            if fin:
                valuation = {
                    "report_date": str(fin[0]), "pe": fin[1], "pb": fin[2], "ps": fin[3],
                    "roe": fin[4], "eps": fin[5], "total_market_cap": fin[6],
                    "revenue": fin[7], "net_profit": fin[8],
                    "yoy_revenue": fin[9], "yoy_profit": fin[10],
                }

        # 所属行业（stock_industry，可能无覆盖）
        industries: list = []
        if _has_table("stock_industry"):
            industries = [
                {"sw_code": r[0], "industry_name": r[1], "industry_type": r[2]}
                for r in conn.execute(
                    "SELECT sw_code, industry_name, industry_type FROM stock_industry "
                    "WHERE stock_code=? ORDER BY industry_type", [code]).fetchall()
            ]

        # 最新因子快照（factors_daily）
        factors = None
        if _has_table("factors_daily"):
            fr = conn.execute(
                "SELECT trade_date, mom_5d, mom_20d, mom_60d, vol_20d, "
                "       volume_ratio_20d, price_position_20d, bias_ma5, bias_ma20, bias_ma60 "
                "FROM factors_daily WHERE stock_code=? ORDER BY trade_date DESC LIMIT 1", [code],
            ).fetchone()
            if fr:
                factors = {
                    "trade_date": str(fr[0]), "mom_5d": fr[1], "mom_20d": fr[2],
                    "mom_60d": fr[3], "vol_20d": fr[4], "volume_ratio_20d": fr[5],
                    "price_position_20d": fr[6], "bias_ma5": fr[7],
                    "bias_ma20": fr[8], "bias_ma60": fr[9],
                }

        return {
            "stock_code": basic[0], "short_name": basic[1], "exchange": basic[2],
            "list_date": str(basic[3]) if basic[3] else None,
            "quote": quote, "stats": stats,
            "valuation": valuation, "industries": industries, "factors": factors,
        }

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
