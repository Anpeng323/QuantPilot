"""
RAG 完整链路：载入 → 切片 → embedding → 存向量库 → 检索 → 拼 prompt → 调 LLM。
"""
from __future__ import annotations
import uuid
import json
import re
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from etl.db import get_conn
from knowledge.loader import load
from knowledge.chunker import chunk_segments, Chunk
from knowledge.embedder import embed_texts, embed_query
from knowledge.vector_store import upsert_chunks, delete_doc, query as vec_query
from ai import client as llm
from ai import router
from ai.prompts import PROMPT_RAG


def _tokenize(t: str) -> List[str]:
    return [w for w in re.split(r"[\s,，。、.;；:：()（）\"']+", t or "")
            if re.match(r"[\u4e00-\u9fffA-Za-z0-9]+", w)]


def _rerank(results: List[Dict[str, Any]], query_text: str,
            score_floor: float = 0.6, top_k: int = 5) -> List[Dict[str, Any]]:
    """本地 rerank（不接付费服务）：相似度阈值过滤 + metadata 轻量 boosting 重排。

    - 丢弃 similarity < score_floor 的片段（降噪）
    - boosting：report/announcement 权重更高；标题/section 词重叠越多加分越多
    """
    if not results:
        return []
    q_tok = set(_tokenize(query_text))
    boosted = []
    for r in results:
        s = r.get("score", 0)
        if s < score_floor:
            continue
        boost = 0.0
        dt = r.get("doc_type", "") or ""
        if dt in ("report", "announcement"):
            boost += 0.06
        elif dt in ("research", "note"):
            boost += 0.03
        title = r.get("section") or r.get("title") or ""
        overlap = len(q_tok & set(_tokenize(title))) if q_tok else 0
        boost += min(overlap * 0.02, 0.1)
        boosted.append((r, min(s + boost, 1.0)))
    boosted.sort(key=lambda x: x[1], reverse=True)
    out = []
    for r, sc in boosted[:top_k]:
        r = dict(r)
        r["score"] = round(sc, 4)
        out.append(r)
    return out


def _enrich_docs(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """补充 doc 表里的标题/发布日期，供前端来源标注展开。"""
    doc_ids = list({r.get("doc_id") for r in results if r.get("doc_id")})
    if not doc_ids:
        return results
    try:
        rows = get_conn().execute(
            "SELECT doc_id, title, publish_date FROM doc WHERE doc_id IN ({})".format(
                ",".join("?" * len(doc_ids))), doc_ids).fetchall()
        meta = {row[0]: {"title": row[1], "date": str(row[2] or "")} for row in rows}
    except Exception:
        return results
    for r in results:
        m = meta.get(r.get("doc_id"))
        if m:
            r["title"] = m["title"]
            r["date"] = m["date"]
    return results


def ingest_file(file_path: str, doc_type: str = "pdf", source: str = "manual",
                stock_code: Optional[str] = None, title: Optional[str] = None,
                publish_date: Optional[str] = None,
                url: Optional[str] = None,
                industry_code: Optional[str] = None) -> Dict[str, Any]:
    """
    完整入库流程：载入 → 切片 → embedding → 写入向量库 → 写 doc 元数据到 DuckDB。
    返回 {doc_id, chunk_count, status, ...}
    """
    if not Path(file_path).exists():
        return {"error": f"文件不存在: {file_path}"}

    doc_id = uuid.uuid4().hex[:16]
    if title is None:
        title = Path(file_path).stem

    # 1. 解析
    t0 = time.time()
    try:
        segs = load(file_path)
    except Exception as e:
        return {"error": f"解析失败: {e}"}

    if not segs:
        return {"error": "文档解析为空（可能是扫描件 PDF，需要 OCR）"}

    # 2. 切片
    chunks = chunk_segments(segs, doc_id=doc_id)
    if not chunks:
        return {"error": "切片后为空"}

    # 3. Embedding
    try:
        embs = embed_texts([c.text for c in chunks])
    except Exception as e:
        return {"error": f"向量化失败: {e}（模型可能未下载）"}

    # 4. 写向量库
    for c in chunks:
        c.metadata["stock_code"] = stock_code or ""
        c.metadata["doc_type"]   = doc_type
    n = upsert_chunks(chunks, embs)

    # 5. 写 DuckDB doc 元数据
    conn = get_conn()
    conn.execute("""
        INSERT INTO doc
        (doc_id, doc_type, source, stock_code, industry_code, title, publish_date,
         file_path, url, chunk_count, ingested_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp, ?)
    """, [doc_id, doc_type, source, stock_code or "", industry_code or "",
          title, publish_date, file_path, url or "", n,
          json.dumps({"size_bytes": Path(file_path).stat().st_size})])
    dur = round(time.time() - t0, 2)
    return {
        "doc_id": doc_id, "chunk_count": n,
        "status": "ok", "duration_sec": dur,
        "title": title, "stock_code": stock_code,
    }


def delete_doc_full(doc_id: str) -> Dict[str, Any]:
    """删除一个文档（向量 + 元数据）"""
    n = delete_doc(doc_id)
    conn = get_conn()
    conn.execute("DELETE FROM doc WHERE doc_id = ?", [doc_id])
    return {"doc_id": doc_id, "deleted_chunks": n, "status": "ok"}


def query(question: str, stock_code: Optional[str] = None, top_k: int = 5,
          stock_codes: Optional[List[str]] = None,
          doc_types: Optional[List[str]] = None,
          model: str = None) -> Dict[str, Any]:
    """
    RAG 问答。
    1. Embedding 问题
    2. 向量库召回（支持单股票/多股票/多文档类型过滤）
    4. 调 LLM 回答
    """
    if not question.strip():
        return {"error": "问题为空"}

    try:
        q_emb = embed_query(question)
    except Exception as e:
        return {"error": f"问题向量化失败: {e}"}

    retrieved_raw = vec_query(q_emb, top_k=max(top_k * 3, 20), stock_code=stock_code,
                              stock_codes=stock_codes, doc_types=doc_types)
    if not retrieved_raw:
        return {
            "answer": "**知识库为空或未检索到相关内容**。请先上传 PDF/Word/Markdown 文档，或放宽检索条件。",
            "citations": [],
            "model": "none",
            "duration_ms": 0,
        }

    # 本地 rerank + 来源补全
    retrieved = _enrich_docs(_rerank(retrieved_raw, question, top_k=top_k))
    if not retrieved:
        return {
            "answer": "**未检索到高相关内容**（相似度低于阈值）。请换一种问法或上传更对口的资料。",
            "citations": [],
            "model": "none",
            "duration_ms": 0,
        }

    # 拼接参考资料
    ctx_parts = []
    for i, r in enumerate(retrieved, 1):
        ctx_parts.append(f"### [{i}] {r['doc_id']}  page {r['page']}  (score={r['score']})\n{r['text'][:1500]}")
    ctx = "\n\n".join(ctx_parts)

    from jinja2 import Template
    prompt = Template(PROMPT_RAG).render(question=question, context=ctx)

    t0 = time.time()
    try:
        resp = router.chat_for_task(router.TASK_RAG_QA,
                                    [{"role": "user", "content": prompt}],
                                    temperature=0.2, max_tokens=2000)
    except Exception as e:
        return {
            "answer": "**知识库检索完成，但大模型调用失败，请稍后重试。**",
            "citations": [{
                "chunk_id": r["chunk_id"], "doc_id": r["doc_id"],
                "doc_type": r.get("doc_type", ""), "page": r["page"],
                "section": r["section"], "stock_code": r["stock_code"],
                "score": r["score"], "text_preview": r["text"][:200],
            } for r in retrieved],
            "model": "none", "tokens": 0, "duration_ms": int((time.time() - t0) * 1000),
            "degraded": True,
        }
    dur = int((time.time() - t0) * 1000)
    top_score = max((r["score"] for r in retrieved), default=0.0)
    confidence = round(min(0.99, max(0.3, top_score)), 2)

    return {
        "answer": resp["text"],
        "citations": [{
            "chunk_id": r["chunk_id"],
            "doc_id": r["doc_id"],
            "doc_type": r.get("doc_type", ""),
            "page": r["page"],
            "section": r["section"],
            "stock_code": r["stock_code"],
            "title": r.get("title", ""),
            "date": r.get("date", ""),
            "score": r["score"],
            "text_preview": r["text"][:200],
        } for r in retrieved],
        "model": resp["model"],
        "tokens": resp["total_tokens"],
        "duration_ms": dur,
        "confidence": confidence,
    }


_PROMPT_RAG_COMPARE = """你是一名资深证券分析师。以下是用户就多只股票提出的对比问题。

## 结构化财务对比
{{table}}

## 来自研报/公告/笔记的检索资料
{{context}}

## 用户问题
{{question}}

要求：
- 紧扣问题，结合财务数据与检索资料做横向对比；
- 给出明确结论与关键差异点；
- 引用资料时标注 [来源 doc_id=xxx]；
- 若数据不足，明确指出缺失项，不要编造。
"""


def compare(stock_codes: List[str], question: str, top_k: int = 4,
            model: str = None) -> Dict[str, Any]:
    """
    多股票 RAG 对比问答：结构化财务对比表 + 研报/公告/笔记检索资料 → LLM 横向对比。
    """
    if not question.strip():
        return {"error": "问题为空"}
    if not stock_codes:
        return {"error": "请至少提供 1 只股票（stock_codes）"}

    # 1. 结构化财务对比表（复用 comparative 分析器）
    try:
        from ai.analyzers.comparative import build_context as cmp_ctx
        _rows, table_ctx = cmp_ctx(stock_codes)
    except Exception as e:
        table_ctx = f"（结构化对比生成失败：{e}）"

    # 2. RAG 检索（仅该组股票的 研报/公告/笔记）
    try:
        q_emb = embed_query(question)
    except Exception as e:
        return {"error": f"问题向量化失败: {e}"}
    retrieved_raw = vec_query(
        q_emb, top_k=max(12, top_k * len(stock_codes) * 2),
        stock_codes=stock_codes,
        doc_types=["report", "announcement", "research", "note"],
    )
    retrieved = _enrich_docs(_rerank(retrieved_raw, question,
                                     score_floor=0.6, top_k=top_k * len(stock_codes)))
    ctx_parts = []
    for i, r in enumerate(retrieved, 1):
        ctx_parts.append(
            f"### [{i}] doc_id={r['doc_id']} stock={r['stock_code']} "
            f"page {r['page']} (score={r['score']})\n{r['text'][:1200]}"
        )
    ctx = "\n\n".join(ctx_parts) if ctx_parts else "（知识库无该组股票相关文档）"

    from jinja2 import Template
    prompt = Template(_PROMPT_RAG_COMPARE).render(
        table=table_ctx, context=ctx, question=question)

    t0 = time.time()
    try:
        resp = router.chat_for_task(router.TASK_RAG_QA,
                                    [{"role": "user", "content": prompt}],
                                    temperature=0.3, max_tokens=2200)
    except Exception as e:
        return {
            "answer": f"**对比分析失败（大模型调用异常）：{e}**",
            "citations": [], "stock_codes": stock_codes,
            "model": "none", "tokens": 0, "duration_ms": int((time.time() - t0) * 1000),
            "degraded": True,
        }
    dur = int((time.time() - t0) * 1000)
    top_score = max((r["score"] for r in retrieved), default=0.0)
    confidence = round(min(0.99, max(0.3, top_score)), 2)

    return {
        "answer": resp["text"],
        "citations": [{
            "chunk_id": r["chunk_id"],
            "doc_id": r["doc_id"],
            "doc_type": r.get("doc_type", ""),
            "stock_code": r["stock_code"],
            "title": r.get("title", ""),
            "date": r.get("date", ""),
            "page": r["page"],
            "score": r["score"],
            "text_preview": r["text"][:200],
        } for r in retrieved],
        "model": resp["model"],
        "tokens": resp["total_tokens"],
        "duration_ms": dur,
        "confidence": confidence,
        "stock_codes": stock_codes,
    }
