"""
统一向量入库（news / announcement / note / manual 复用）。

核心：文本 → 切片 → embedding → 写 Chroma，并返回 chunk_count。
这样所有文档类型走同一套「切片+向量化」逻辑，避免各自实现导致
news / announcement 只存元数据、不进向量库的问题。
"""
from __future__ import annotations
from typing import List, Tuple

from knowledge.loader import DocSegment
from knowledge.chunker import chunk_segments
from knowledge.embedder import embed_texts
from knowledge.vector_store import upsert_chunks, delete_doc


def _build_chunks(doc_id, text, *, doc_type="", stock_code="", source="", title="",
                  chunk_size=512, overlap=64):
    if not text or not text.strip():
        return []
    segs = [DocSegment(text=text, section=title or "")]
    chunks = chunk_segments(segs, doc_id=doc_id, chunk_size=chunk_size, overlap=overlap)
    for c in chunks:
        c.metadata["stock_code"] = stock_code or ""
        c.metadata["doc_type"]   = doc_type or ""
        c.metadata["source"]     = source or ""
        c.metadata["title"]      = title or ""
    return chunks


def embed_doc(doc_id, text, *, doc_type="", stock_code="", source="", title="") -> int:
    """单篇入库：文本 → 切片 → embedding → Chroma。返回 chunk 数。"""
    chunks = _build_chunks(doc_id, text, doc_type=doc_type, stock_code=stock_code,
                           source=source, title=title)
    if not chunks:
        return 0
    embs = embed_texts([c.text for c in chunks])
    return upsert_chunks(chunks, embs)


def embed_batch(items, *, doc_type="", source="", chunk_size=512, overlap=64) -> List[int]:
    """批量入库：items = [(doc_id, text, stock_code, title), ...]。

    统一切片 + 一次性 embedding（比逐条调用快很多），返回每篇的 chunk_count。
    """
    index = []
    all_chunks = []
    for doc_id, text, stock_code, title in items:
        chunks = _build_chunks(doc_id, text, doc_type=doc_type, stock_code=stock_code,
                               source=source, title=title,
                               chunk_size=chunk_size, overlap=overlap)
        index.append(chunks)
        all_chunks.extend(chunks)
    if not all_chunks:
        return [0] * len(items)
    embs = embed_texts([c.text for c in all_chunks])
    upsert_chunks(all_chunks, embs)
    return [len(chs) for chs in index]


def reembed_file(doc_id, file_path, *, doc_type="", stock_code="", source="", title="") -> int:
    """从本地文件重新向量化（复用现有 doc_id，先删旧向量再写）。返回 chunk 数。"""
    from knowledge.loader import load
    try:
        segs = load(file_path)
    except Exception as e:
        print(f"  ! 加载失败 {file_path}: {e}")
        return 0
    if not segs:
        return 0
    delete_doc(doc_id)
    chunks = chunk_segments(segs, doc_id=doc_id)
    if not chunks:
        return 0
    for c in chunks:
        c.metadata["stock_code"] = stock_code or ""
        c.metadata["doc_type"]   = doc_type or ""
        c.metadata["source"]     = source or ""
        c.metadata["title"]      = title or ""
    embs = embed_texts([c.text for c in chunks])
    return upsert_chunks(chunks, embs)


def fetch_url_text(url: str, timeout: int = 6) -> str:
    """best-effort 抓取网页正文（公告/资讯详情），失败返回空串。"""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return ""
    try:
        import requests
        resp = requests.get(url, timeout=timeout,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        paras = [p.get_text(strip=True) for p in soup.find_all("p")]
        text = "\n".join(p for p in paras if len(p) > 20)
        return text[:4000]
    except Exception:
        return ""
