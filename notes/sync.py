"""
笔记 ↔ 向量库同步。
- 把每条笔记作为一个独立 doc 入向量库
- doc_type="note"、source="manual"
- 笔记 ID 即 chunk_id（每条笔记作为一个 chunk；超过长度则再切片）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl.db import get_conn
from knowledge.vector_store import upsert_chunks, delete_doc
from knowledge.chunker import Chunk
from knowledge.embedder import embed_texts
from knowledge.chunker import chunk_segments
from knowledge.loader import DocSegment


def sync_note(note_id: str) -> dict:
    """同步单条笔记到向量库"""
    from notes.note import get_note
    n = get_note(note_id)
    if not n:
        return {"error": f"笔记不存在 {note_id}"}

    # 先删旧的（如果有）
    delete_doc(note_id)

    # 把 Markdown 当作一段文本，切片（笔记通常较短，1 个 chunk 即可）
    segs = [DocSegment(text=n["content_md"] or "", section=n["title"])]
    chunks = chunk_segments(segs, doc_id=note_id)
    if not chunks:
        return {"error": "笔记内容为空"}
    for c in chunks:
        c.metadata["stock_code"] = n["stock_code"]
        c.metadata["doc_type"]   = "note"
        c.metadata["note_id"]    = note_id
        c.metadata["title"]      = n["title"]

    try:
        embs = embed_texts([c.text for c in chunks])
    except Exception as e:
        return {"error": f"embedding 失败: {e}"}

    n_chunks = upsert_chunks(chunks, embs)

    # 同时把笔记本身登记到 doc 表
    from datetime import datetime
    conn = get_conn()
    # 先删旧的（id 冲突）
    conn.execute("DELETE FROM doc WHERE doc_id=? AND doc_type='note'", [note_id])
    conn.execute("""
        INSERT INTO doc (doc_id, doc_type, source, stock_code, title, file_path, chunk_count, ingested_at)
        VALUES (?, 'note', 'manual', ?, ?, ?, ?, current_timestamp)
    """, [note_id, n["stock_code"], f"[note] {n['title']}", f"note://{note_id}", n_chunks])
    # 更新笔记 synced 标记
    conn.execute("UPDATE note SET synced_to_vector=true, embedding_id=? WHERE note_id=?",
                 [f"{note_id}#0000", note_id])
    return {"note_id": note_id, "chunk_count": n_chunks, "status": "synced"}


def remove_note_from_vector(note_id: str) -> int:
    """把笔记从向量库移除"""
    n = delete_doc(note_id)
    conn = get_conn()
    conn.execute("DELETE FROM doc WHERE doc_id=? AND doc_type='note'", [note_id])
    conn.execute("UPDATE note SET synced_to_vector=false WHERE note_id=?", [note_id])
    return n
