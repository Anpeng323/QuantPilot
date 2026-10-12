"""
RAG 完整链路：载入 → 切片 → embedding → 存向量库 → 检索 → 拼 prompt → 调 LLM。
"""
from __future__ import annotations
import uuid
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from etl.db import get_conn
from knowledge.loader import load
from knowledge.chunker import chunk_segments, Chunk
from knowledge.embedder import embed_texts, embed_query
from knowledge.vector_store import upsert_chunks, delete_doc, query as vec_query
from ai import client as llm
from ai.prompts import PROMPT_RAG


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
          model: str = None) -> Dict[str, Any]:
    """
    RAG 问答。
    1. Embedding 问题
    2. 向量库召回
    4. 调 LLM 回答
    """
    if not question.strip():
        return {"error": "问题为空"}

    try:
        q_emb = embed_query(question)
    except Exception as e:
        return {"error": f"问题向量化失败: {e}"}

    retrieved = vec_query(q_emb, top_k=top_k, stock_code=stock_code)
    if not retrieved:
        return {
            "answer": "**知识库为空**。请先在 RAG 页面上传 PDF/Word/Markdown 文档。",
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
    resp = llm.chat([{"role": "user", "content": prompt}],
                    model=model, temperature=0.2, max_tokens=2000)
    dur = int((time.time() - t0) * 1000)

    return {
        "answer": resp["text"],
        "citations": [{
            "chunk_id": r["chunk_id"],
            "doc_id": r["doc_id"],
            "page": r["page"],
            "section": r["section"],
            "stock_code": r["stock_code"],
            "score": r["score"],
            "text_preview": r["text"][:200],
        } for r in retrieved],
        "model": resp["model"],
        "tokens": resp["total_tokens"],
        "duration_ms": dur,
    }
