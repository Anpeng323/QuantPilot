"""
AI 投研平台的所有 API 路由（接续 web/server.py）
- 自选池 / 笔记 / RAG / AI 分析 / 持仓 / 复盘 / 数据接入
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import os, json, uuid, queue, threading
from pathlib import Path

from web.db import get_conn
from ai import client as llm_client
from ai import memory as mem
from ai.analyzers import run as run_analyzer
from ai.analyzers.comprehensive import run as run_comprehensive
from ai.analyzers.financial import run as run_financial
from ai.analyzers.risk import run as run_risk
from ai.analyzers.valuation import run as run_valuation
from ai.analyzers.sentiment import run as run_sentiment
from ai.analyzers.news import (
    run as run_news_analysis,
    get_analysis as get_news_analysis,
    run_for_stock as run_news_for_stock,
)
from knowledge import rag_chain
from knowledge import vector_store as vs
from knowledge.embedder import warmup, info as emb_info
from watchlist import pool as wl_pool
from watchlist import alerts as wl_alerts
from notes import note as note_mod
from notes import sync as note_sync
from portfolio import position as pf_pos, trade as pf_tr, review as pf_rv
from data_ingest import announcement as di_ann, news as di_news, finance as di_fin
from data_ingest import industry_chain as di_chain

router = APIRouter(prefix="/api/v1", tags=["ai-invest"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# =============== 1. LLM 信息 ===============
@router.get("/llm/info")
def llm_info():
    return {"llm": llm_client.provider_info(), "embedding": emb_info()}


@router.post("/llm/embed-warmup")


@router.get("/schema")
def schema_overview():
    """返回所有表的列结构（供 SQL 面板展示）"""
    conn = get_conn()
    tables = conn.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='main' AND table_name NOT LIKE 'sqlite_%'
        ORDER BY table_name
    """).fetchall()
    result = []
    for (t,) in tables:
        cols = conn.execute(f"DESCRIBE {t}").fetchall()
        row_count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        result.append({
            "table": t,
            "row_count": row_count,
            "columns": [{"name": c[0], "type": c[1], "nullable": c[2]} for c in cols],
        })
    return result

@router.get("/llm/models")
def llm_list_models():
    """列出当前 provider 可用模型（用于调试/切换）"""
    if llm_client.PROVIDER == "minimax":
        return {"provider": "minimax", "models": llm_client.list_minimax_models()}
    return {"provider": llm_client.PROVIDER, "models": [llm_client._price_per_1k.__name__ or ""]}

def llm_warmup():
    msg = warmup()
    return {"status": "ok" if msg is True else "fail", "msg": str(msg)}


# =============== 2. 自选池 ===============
@router.get("/watchlist/groups")
def wl_groups():
    return wl_pool.list_groups()


@router.post("/watchlist/groups")
def wl_group_create(body: dict):
    return wl_pool.create_group(body.get("group_name", ""),
                                body.get("description", ""),
                                body.get("sort_order", 0))


@router.delete("/watchlist/groups/{group_name}")
def wl_group_delete(group_name: str):
    return wl_pool.delete_group(group_name)


@router.get("/watchlist/{group_name}")
def wl_items(group_name: str):
    return wl_pool.list_group_items(group_name)


@router.post("/watchlist/{group_name}/{stock_code}")
def wl_add(group_name: str, stock_code: str, body: dict = None):
    body = body or {}
    return wl_pool.add_to_group(group_name, stock_code, body.get("note", ""))


@router.delete("/watchlist/{group_name}/{stock_code}")
def wl_remove(group_name: str, stock_code: str):
    return wl_pool.remove_from_group(group_name, stock_code)


@router.get("/watchlist/{group_name}/alerts")
def wl_alerts_for_group(group_name: str):
    items = wl_pool.list_group_items(group_name)
    return [{"stock_code": it["stock_code"], "alerts": wl_alerts.check_alerts(it["stock_code"])} for it in items]


# =============== 3. 笔记 ===============
@router.get("/notes")
def notes_list(stock_code: str = "", limit: int = 50):
    return note_mod.list_notes(stock_code=stock_code, limit=limit)


@router.post("/notes")
def notes_create(body: dict):
    return note_mod.create_note(
        stock_code=body.get("stock_code", ""),
        title=body.get("title", "无标题"),
        content_md=body.get("content_md", ""),
        tags=body.get("tags", []),
    )


@router.get("/notes/{note_id}")
def notes_get(note_id: str):
    n = note_mod.get_note(note_id)
    if not n:
        raise HTTPException(404, f"找不到笔记 {note_id}")
    return n


@router.put("/notes/{note_id}")
def notes_update(note_id: str, body: dict):
    return note_mod.update_note(note_id,
                                title=body.get("title"),
                                content_md=body.get("content_md"),
                                tags=body.get("tags"))


@router.delete("/notes/{note_id}")
def notes_delete(note_id: str):
    return note_mod.delete_note(note_id)


@router.post("/notes/{note_id}/sync-vector")
def notes_sync_vector(note_id: str):
    return note_sync.sync_note(note_id)


# =============== 4. RAG ===============
@router.get("/rag/stats")
def rag_stats():
    return {"vector_store": vs.info(),
            "embed_model": emb_info()}


@router.post("/rag/upload")
async def rag_upload(
    file: UploadFile = File(...),
    stock_code: str = Form(""),
    doc_type: str = Form("pdf"),
    title: str = Form(""),
):
    """上传 PDF/Word/MD，自动切片 + 向量化"""
    ext = Path(file.filename).suffix.lower()
    if ext not in (".pdf", ".docx", ".md", ".markdown", ".txt", ".html", ".htm"):
        return JSONResponse({"error": f"不支持的文件类型 {ext}"}, status_code=400)

    # 保存到 data/uploads/
    save_path = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}{ext}"
    save_path.write_bytes(await file.read())

    result = rag_chain.ingest_file(
        file_path=str(save_path),
        doc_type=doc_type,
        source="manual",
        stock_code=stock_code or None,
        title=title or None,
    )
    return result


@router.get("/rag/docs")
def rag_docs_list(stock_code: str = "", limit: int = 100):
    conn = get_conn()
    sql = "SELECT doc_id, doc_type, source, stock_code, title, publish_date, file_path, chunk_count, ingested_at FROM doc"
    params = []
    if stock_code:
        sql += " WHERE stock_code = ?"
        params.append(stock_code)
    sql += " ORDER BY ingested_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    from etl.industry import enrich_with_board
    items = [{
        "doc_id": r[0], "doc_type": r[1], "source": r[2], "stock_code": r[3],
        "title": r[4], "publish_date": str(r[5]) if r[5] else "",
        "file_path": r[6], "chunk_count": r[7], "ingested_at": str(r[8]),
    } for r in rows]
    enrich_with_board(items)
    return items


@router.delete("/rag/docs/{doc_id}")
def rag_doc_delete(doc_id: str):
    return rag_chain.delete_doc_full(doc_id)


@router.post("/rag/query")
def rag_query(body: dict):
    return rag_chain.query(
        question=body.get("question", ""),
        stock_code=body.get("stock_code") or None,
        stock_codes=body.get("stock_codes"),
        doc_types=body.get("doc_types"),
        top_k=body.get("top_k", 5),
    )


@router.post("/rag/compare")
def rag_compare(body: dict):
    """多股票 RAG 对比问答：结构化财务对比 + 研报/公告检索 → 横向对比。"""
    return rag_chain.compare(
        stock_codes=body.get("stock_codes", []),
        question=body.get("question", ""),
        top_k=body.get("top_k", 4),
    )


# =============== 5. AI 分析 ===============
@router.post("/ai/analyze")
def ai_analyze(body: dict):
    # 兼容前端传 analysis_type 与 type 两种字段（此前只读 type，导致类型选择不生效）
    return run_analyzer(
        stock_code=body.get("stock_code", ""),
        analysis_type=body.get("type") or body.get("analysis_type") or "financial",
        stock_codes=body.get("stock_codes"),
    )


@router.post("/ai/analyze/stream")
def ai_analyze_stream(body: dict):
    """流式 AI 分析：以 SSE 推送流程进展，避免前端干等。

    事件流：
      event: stage    data: {"stage": "prepare"|"llm"|"render", "label": str, "pct": int}
      event: result   data: {<run_analyzer 的完整返回>}
      event: error    data: {"message": str}
    """
    q: "queue.Queue" = queue.Queue()

    def on_progress(stage: str, label: str):
        pct = {"prepare": 20, "llm": 70, "render": 95}.get(stage, 50)
        q.put({"event": "stage", "data": {"stage": stage, "label": label, "pct": pct}})

    def worker():
        try:
            llm_client.set_progress(on_progress)
            on_progress("prepare", "📊 正在读取财务 / 行情 / 因子数据 …")
            result = run_analyzer(
                stock_code=body.get("stock_code", ""),
                analysis_type=body.get("type") or body.get("analysis_type") or "financial",
                stock_codes=body.get("stock_codes"),
            )
            q.put({"event": "result", "data": result})
        except Exception as e:  # 异常也推给前端
            q.put({"event": "error", "data": {"message": str(e)}})
        finally:
            llm_client.set_progress(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            ev = item["event"]
            yield f"event: {ev}\ndata: {json.dumps(item['data'], ensure_ascii=False, default=str)}\n\n"
            if ev in ("result", "error"):
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# =============== 5b. 综合投研报告（统一，不区分类型） ===============
@router.post("/ai/analyze/comprehensive/stream")
def ai_analyze_comprehensive_stream(body: dict):
    """流式综合报告：SSE 推送「准备→检索→撰写→整理」全过程，并检索内外部资料。

    事件流同 /ai/analyze/stream：
      event: stage    data: {"stage": str, "label": str, "pct": int}
      event: result   data: {<comprehensive.run 返回>}
      event: error    data: {"message": str}
    """
    q: "queue.Queue" = queue.Queue()
    code = body.get("stock_code", "")

    def on_stage(stage: str, label: str, pct: int):
        q.put({"event": "stage", "data": {"stage": stage, "label": label, "pct": pct}})

    def worker():
        try:
            result = run_comprehensive(code, save=True, on_stage=on_stage)
            q.put({"event": "result", "data": result})
        except Exception as e:
            q.put({"event": "error", "data": {"message": str(e)}})

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            ev = item["event"]
            yield f"event: {ev}\ndata: {json.dumps(item['data'], ensure_ascii=False, default=str)}\n\n"
            if ev in ("result", "error"):
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# =============== 4. 投研智能问答 Agent（ReAct 编排） ===============
@router.post("/agent/ask")
def agent_ask(body: dict):
    from ai.agent import ask
    import uuid as _uuid
    import json as _json
    question = body.get("question", "")
    result = ask(
        question=question,
        stock_code=body.get("stock_code"),
        history=body.get("history"),
    )
    # 持久化对话历史（便于回看与审计），失败不影响返回
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO agent_session (session_id, question, answer, citations_json, "
            "confidence, investment_view, model_used) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [_uuid.uuid4().hex[:16], question[:2000], (result.get("answer") or "")[:8000],
             _json.dumps(result.get("citations", []), ensure_ascii=False),
             result.get("confidence"), bool(result.get("investment_view")),
             result.get("model", "")],
        )
    except Exception:
        pass
    return result


@router.get("/ai/history")
def ai_history(stock_code: str = "", limit: int = 20):
    conn = get_conn()
    sql = """SELECT analysis_id, stock_code, analysis_type, model_used,
                   tokens_used, cost_cny, duration_ms, created_at
            FROM ai_analysis"""
    params = []
    if stock_code:
        sql += " WHERE stock_code = ?"
        params.append(stock_code)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [{
        "analysis_id": r[0], "stock_code": r[1], "type": r[2],
        "model": r[3], "tokens": r[4], "cost_cny": r[5],
        "duration_ms": r[6], "created_at": str(r[7]),
    } for r in rows]


@router.get("/ai/result/{analysis_id}")
def ai_result(analysis_id: str):
    conn = get_conn()
    row = conn.execute("""
        SELECT analysis_id, stock_code, analysis_type, prompt, response,
               citations, model_used, tokens_used, cost_cny, duration_ms, created_at
        FROM ai_analysis WHERE analysis_id=?
    """, [analysis_id]).fetchone()
    if not row:
        raise HTTPException(404, "找不到分析记录")
    return {
        "analysis_id": row[0], "stock_code": row[1], "type": row[2],
        "prompt": row[3], "response": row[4],
        "citations": json.loads(row[5]) if row[5] else [],
        "model": row[6], "tokens": row[7], "cost_cny": row[8],
        "duration_ms": row[9], "created_at": str(row[10]),
    }


# =============== 6. 持仓 / 交易 / 复盘 ===============
@router.get("/portfolio/positions")
def pf_list_pos(status: str = "open"):
    return pf_pos.list_positions(status=status)


@router.post("/portfolio/positions")
def pf_add_pos(body: dict):
    return pf_pos.add_position(
        stock_code=body.get("stock_code", ""),
        cost_price=float(body.get("cost_price", 0)),
        quantity=int(body.get("quantity", 0)),
        open_date=body.get("open_date"),
        note=body.get("note", ""),
    )


@router.post("/portfolio/positions/{position_id}/close")
def pf_close_pos(position_id: str):
    return pf_pos.close_position(position_id)


@router.get("/portfolio/trades")
def pf_list_trades(stock_code: str = "", limit: int = 100):
    return pf_tr.list_trades(stock_code=stock_code, limit=limit)


@router.post("/portfolio/trades")
def pf_add_trade(body: dict):
    return pf_tr.add_trade(
        stock_code=body.get("stock_code", ""),
        direction=body.get("direction", "buy"),
        price=float(body.get("price", 0)),
        quantity=int(body.get("quantity", 0)),
        trade_date=body.get("trade_date"),
        logic_text=body.get("logic_text", ""),
        note_id=body.get("note_id", ""),
    )


@router.post("/portfolio/review")
def pf_run_review(body: dict):
    return pf_rv.run_review(period=body.get("period", "all"))


@router.get("/portfolio/reviews")
def pf_list_reviews(limit: int = 10):
    return pf_rv.list_reviews(limit=limit)


# =============== 7. 用户偏好 ===============
@router.get("/memory")
def memory_get():
    return mem.load_memory()


@router.post("/memory")
def memory_save(body: dict):
    return mem.update_memory(body)


# =============== 8. 数据接入（手动触发） ===============
@router.post("/ingest/news")
def ingest_news(body: dict):
    return di_news.add_news(
        title=body.get("title", ""),
        url=body.get("url", ""),
        summary=body.get("summary", ""),
        source=body.get("source", ""),
        publish_date=body.get("publish_date"),
    )


@router.post("/ingest/news/crawl")
def ingest_news_crawl(body: dict = None):
    """手动触发聚合抓取（多来源）。body: {sources?: list[str], limit?: int, stock_code?: str}"""
    body = body or {}
    sources = body.get("sources") or None
    if isinstance(sources, str):
        sources = [s.strip() for s in sources.split(",") if s.strip()] or None
    limit = int(body.get("limit", 100))
    stock_code = body.get("stock_code", "")
    return di_news.run(limit=limit, stock_code=stock_code, sources=sources)


@router.get("/ingest/news")
def ingest_news_list(limit: int = 30, source: str = "", tag: str = "",
                     stock_code: str = "", q: str = "", page: int = 1):
    """新闻聚合列表（多维度筛选 + 分页）。"""
    return di_news.list_news(limit=limit, stock_code=stock_code, source=source,
                             tag=tag, q=q, page=page)


@router.get("/stock/{code}/news")
def stock_news(code: str, limit: int = 30, page: int = 1):
    """个股相关新闻（新闻聚合看板的个股关联）。"""
    return di_news.list_news(limit=limit, stock_code=code, page=page)


@router.post("/news/analyze")
def news_analyze(body: dict):
    """对单条新闻做 AI 分析（情绪/摘要/要点/影响/风险/关注点）。

    body: {doc_id: str, force?: bool}；force=True 时忽略缓存强制重算。
    """
    doc_id = body.get("doc_id", "")
    if not doc_id:
        return JSONResponse(status_code=400, content={"error": "doc_id 必填"})
    return run_news_analysis(doc_id, save=True, force=bool(body.get("force", False)))


@router.get("/news/analyze/stock/{code}")
def news_analyze_stock(code: str):
    """批量分析某股票全部新闻并汇总情绪分布。"""
    return run_news_for_stock(code)


@router.get("/news/analysis/{doc_id}")
def news_analysis_get(doc_id: str):
    """读取已缓存的新闻 AI 分析。"""
    res = get_news_analysis(doc_id)
    if not res:
        return JSONResponse(status_code=404, content={"error": "尚未分析或不存在"})
    return res


@router.get("/ingest/announcements")
def ingest_announcements(stock_code: str = "", limit: int = 50):
    return di_ann.list_announcements(stock_code=stock_code, limit=limit)


@router.post("/ingest/announcements")
def ingest_announcements_crawl(body: dict = None):
    """触发公告抓取（akshare 东方财富，按最近交易日）。body: {days: int, date: str}"""
    body = body or {}
    days = int(body.get("days", 1))
    date_str = body.get("date")
    return di_ann.run(days=days, date_str=date_str)


# =============== 9. 产业链 / 概念图谱 ===============
@router.get("/chain/related")
def chain_related(stock_code: str = "", top_n: int = 10):
    """与某股票共享板块最多的相关股票（共行业/共概念 = 相关）。"""
    if not stock_code:
        return {"error": "stock_code 必填"}
    return {
        "stock_code": stock_code,
        "related": di_chain.related_stocks(stock_code, top_n=top_n),
    }


@router.get("/chain/sector")
def chain_sector(sector: str = "", limit: int = 200):
    """某板块（名称或代码）的成员股票。"""
    if not sector:
        return {"error": "sector 必填"}
    return {"sector": sector, "members": di_chain.sector_members(sector, limit=limit)}


@router.get("/chain/stats")
def chain_stats():
    conn = get_conn()
    edges = conn.execute("SELECT COUNT(*) FROM industry_chain").fetchone()[0]
    boards = conn.execute("SELECT COUNT(*) FROM industry_chain_meta").fetchone()[0]
    done = conn.execute("SELECT COUNT(*) FROM industry_chain_meta WHERE done=true").fetchone()[0]
    return {"sector_edges": edges, "sector_boards": boards, "boards_done": done}
