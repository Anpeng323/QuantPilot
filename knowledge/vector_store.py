"""
向量库封装：基于 Chroma（嵌入式，持久化到 data/chroma/）。
每个 collection 对应一个 topic（默认 'quant_db'）。
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import List, Dict, Any, List as _List
import chromadb
from chromadb.config import Settings

CHROMA_DIR = Path(__file__).resolve().parent.parent / "data" / "chroma"
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

COLLECTION_DEFAULT = "quant_db"

_client = None
_collection = None


def get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )
    return _client


def get_collection(name: str = COLLECTION_DEFAULT):
    return get_client().get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def upsert_chunks(chunks: List, embeddings: List[List[float]]) -> int:
    """
    把 chunk 列表写入向量库。
    chunks: 来自 chunker.py 的 List[Chunk]
    embeddings: 长度一致的 List[List[float]]
    """
    if not chunks:
        return 0
    col = get_collection()
    ids = [c.chunk_id for c in chunks]
    docs = [c.text for c in chunks]
    metas = []
    for c in chunks:
        meta = dict(c.metadata or {})
        meta["doc_id"] = c.doc_id
        meta["page"]    = int(c.page or 0)
        meta["section"] = (c.section or "")[:200]
        metas.append(meta)
    col.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)
    return len(ids)


def delete_doc(doc_id: str) -> int:
    col = get_collection()
    # 先查所有 chunk_id
    res = col.get(where={"doc_id": doc_id})
    if res and res["ids"]:
        col.delete(ids=res["ids"])
        return len(res["ids"])
    return 0


def query(query_embedding: List[float], top_k: int = 10,
          stock_code: str | None = None) -> List[Dict[str, Any]]:
    col = get_collection()
    where = None
    if stock_code:
        where = {"stock_code": stock_code}
    res = col.query(query_embeddings=[query_embedding], n_results=top_k, where=where)
    results = []
    for i, chunk_id in enumerate(res["ids"][0]):
        meta = res["metadatas"][0][i] or {}
        score = 1 - res["distances"][0][i]   # cosine distance → similarity
        results.append({
            "chunk_id": chunk_id,
            "doc_id": meta.get("doc_id", ""),
            "page": meta.get("page", 0),
            "section": meta.get("section", ""),
            "stock_code": meta.get("stock_code", ""),
            "text": res["documents"][0][i],
            "score": round(score, 4),
        })
    return results


def count() -> int:
    return get_collection().count()


def info() -> Dict[str, Any]:
    col = get_collection()
    return {"name": col.name, "count": col.count(), "path": str(CHROMA_DIR)}
