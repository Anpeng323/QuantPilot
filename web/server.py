"""
FastAPI 主应用：Quant DB Web 控制台
- 启动：python3 -m web.server   （推荐用 scripts/run_web.sh）
- 端口：8765
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from web import db

app = FastAPI(title="Quant DB Web", version="1.0")

# 只允许本机访问（CORS 防一手）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8765", "http://127.0.0.1:8765"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ---------- 静态资源 ----------
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ---------- 健康检查 ----------
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
    r = db.query_dict(sql, limit=100)
    if "error" in r:
        return r
    latest = db.get_conn().execute(
        "SELECT MAX(trade_date) FROM daily_kline WHERE k_type=1"
    ).fetchone()[0]
    return {"tables": r["rows"], "latest_trade_date": str(latest)}


# ---------- 全市场快照 ----------
@app.get("/api/snapshot")
def snapshot(
    exchange: str | None = Query(None, pattern="^(SH|SZ|BJ)$"),
    sort: str = Query("change_pct", pattern="^(change_pct|amount|volume|close)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, description="名称或代码模糊"),
):
    latest = db.get_latest_trade_date()
    where = ["a.k_type=1", "a.trade_date = ?", "a.volume > 0"]
    params: list = [latest]
    if exchange:
        where.append("b.exchange = ?")
        params.append(exchange)
    if q:
        where.append("(b.short_name LIKE ? OR b.stock_code LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    where_sql = " AND ".join(where)
    # 排序字段做白名单
    sort_col = {
        "change_pct": "a.change_pct",
        "amount":     "a.amount",
        "volume":     "a.volume",
        "close":      "a.close",
    }[sort]
    sql = f"""
        SELECT a.stock_code, b.short_name, b.exchange,
               a.close, a.change, ROUND(a.change_pct, 2) AS change_pct,
               a.volume, a.amount, ROUND(a.amount/1e8, 2) AS amount_yi
        FROM daily_kline a
        JOIN stock_basic b USING (stock_code)
        WHERE {where_sql}
        ORDER BY {sort_col} {order.upper()}
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    r = db.query_dict(sql, params=params, limit=limit + 1)
    if "error" in r:
        return r
    total_sql = f"SELECT COUNT(*) FROM daily_kline a JOIN stock_basic b USING (stock_code) WHERE {where_sql}"
    total_params = params[:-2]
    total = db.get_conn().execute(total_sql, total_params).fetchone()[0]
    return {
        "trade_date": latest,
        "rows": r["rows"][:limit],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ---------- 股票搜索 ----------
@app.get("/api/stocks/search")
def stocks_search(q: str = Query(..., min_length=1), limit: int = Query(20, le=50)):
    sql = """
        SELECT stock_code, short_name, exchange
        FROM stock_basic
        WHERE short_name LIKE ? OR stock_code LIKE ?
        ORDER BY
          CASE WHEN stock_code = ? THEN 0
               WHEN stock_code LIKE ? THEN 1
               WHEN short_name LIKE ? THEN 2
               ELSE 3 END
        LIMIT ?
    """
    pat = f"%{q}%"
    prefix = f"{q}%"
    return db.query_dict(sql, params=[pat, pat, q, prefix, prefix, limit])


# ---------- 个股基本信息 + 最新 ----------
@app.get("/api/stock/{code}")
def stock_info(code: str):
    info = db.get_stock_basic(code)
    if not info:
        return {"error": f"找不到股票 {code}"}
    latest = db.get_latest_trade_date()
    latest_row = db.get_conn().execute(
        "SELECT close, change, ROUND(change_pct,2), volume, amount "
        "FROM daily_kline WHERE stock_code=? AND k_type=1 AND trade_date=?",
        [code, latest]
    ).fetchone()
    info["latest"] = {
        "trade_date": latest,
        "close": latest_row[0] if latest_row else None,
        "change": latest_row[1] if latest_row else None,
        "change_pct": latest_row[2] if latest_row else None,
        "volume": latest_row[3] if latest_row else None,
        "amount": latest_row[4] if latest_row else None,
    }
    return info


# ---------- 个股日 K ----------
@app.get("/api/stock/{code}/daily")
def stock_daily(code: str, start: str | None = None, end: str | None = None,
                limit: int = Query(500, le=2000)):
    latest = db.get_latest_trade_date()
    if end is None:
        end = latest
    if start is None:
        # 默认近 1 年
        start_sql = "SELECT MIN(trade_date) FROM (SELECT trade_date FROM daily_kline "\
                    "WHERE stock_code=? AND k_type=1 AND trade_date <= ? ORDER BY trade_date DESC LIMIT 240)"
        start = str(db.get_conn().execute(start_sql, [code, end]).fetchone()[0])
    sql = """
        SELECT trade_date, open, high, low, close, pre_close, change, change_pct,
               volume, amount
        FROM daily_kline
        WHERE stock_code=? AND k_type=1 AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
    """
    return db.query_dict(sql, params=[code, start, end], limit=limit)


# ---------- 个股分钟 K（5min） ----------
@app.get("/api/stock/{code}/minute")
def stock_minute(code: str, date: str | None = None, limit: int = Query(500, le=2000)):
    if date is None:
        date = db.get_latest_trade_date()
    sql = """
        SELECT trade_time, open, high, low, close, volume, amount
        FROM minute_kline
        WHERE stock_code=? AND k_type=5 AND trade_date=?
        ORDER BY trade_time
    """
    return db.query_dict(sql, params=[code, date], limit=limit)


# ---------- 个股因子 ----------
@app.get("/api/stock/{code}/factors")
def stock_factors(code: str, date: str | None = None, limit: int = Query(120, le=500)):
    if date is None:
        date = db.get_latest_trade_date()
    sql = """
        SELECT trade_date, close, mom_5d, mom_10d, mom_20d, mom_60d,
               vol_20d, vol_60d, volume_ratio_20d, amount_ratio_20d,
               price_position_20d, bias_ma5, bias_ma20, bias_ma60
        FROM factors_daily
        WHERE stock_code=? AND trade_date<=?
        ORDER BY trade_date DESC
        LIMIT ?
    """
    return db.query_dict(sql, params=[code, date, limit])


# ---------- 指数最新 ----------
INDEX_NAMES = {
    "sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000300": "沪深300",  "sh000905": "中证500",  "sh000688": "科创50",
}


@app.get("/api/indices/latest")
def indices_latest():
    latest = db.get_conn().execute(
        "SELECT MAX(trade_date) FROM index_kline"
    ).fetchone()[0]
    sql = """
        SELECT index_code, trade_date, open, high, low, close,
               volume, amount, ROUND(change_pct,2) AS change_pct
        FROM index_kline
        WHERE trade_date = ?
        ORDER BY index_code
    """
    r = db.query_dict(sql, params=[latest])
    if "error" in r:
        return r
    # 不带名字, rows 已经是干净的
    return {
        "trade_date": str(latest),
        "names": INDEX_NAMES,
        "rows": r["rows"],
        "columns": r["columns"],
    }


@app.get("/api/indices/{code}/daily")
def index_daily(code: str, start: str | None = None, end: str | None = None,
                limit: int = Query(2000, le=5000)):
    latest = db.get_latest_trade_date()
    if end is None:
        end = latest
    if start is None:
        start = "2000-01-01"
    sql = """
        SELECT trade_date, open, high, low, close, volume, amount, change_pct
        FROM index_kline
        WHERE index_code=? AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
    """
    return db.query_dict(sql, params=[code, start, end], limit=limit)


# ---------- SQL 面板（只读） ----------
@app.post("/api/sql")
def sql_panel(payload: dict):
    sql = (payload or {}).get("sql", "").strip()
    if not sql:
        return {"error": "请输入 SQL"}
    ok, err = db.safe_sql_check(sql)
    if not ok:
        return {"error": err}
    return db.query_dict(sql, limit=5000, timeout_ms=15000)


# ---------- AI 投研平台路由（接续）----------
from web.ai_routes import router as ai_router
app.include_router(ai_router)


# ---------- main ----------
if __name__ == "__main__":
    import uvicorn
    print("🚀 Quant DB Web 启动中...")
    print("📊 访问 http://localhost:8765  （Ctrl+C 停止）")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
