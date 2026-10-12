"""
文档切片：基于段落的滑动窗口切片。
- 每个 chunk 包含 metadata: {page, section, source}
- 默认 chunk_size=512 tokens，overlap=64
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List
from knowledge.loader import DocSegment


@dataclass
class Chunk:
    text: str
    doc_id: str
    chunk_id: str
    page: int = 0
    section: str = ""
    metadata: dict = field(default_factory=dict)


def _approx_tokens(text: str) -> int:
    """粗略估算 token 数（中英文混合，约 1.5 字符/token）"""
    return max(1, int(len(text) / 1.5))


def chunk_segments(segments: List[DocSegment], doc_id: str,
                   chunk_size: int = 512, overlap: int = 64) -> List[Chunk]:
    """滑动窗口切片"""
    chunks: List[Chunk] = []
    # 先把 segments 拼成连续文本（保留段落边界）
    buf: list = []  # [(text, page, section)]
    for seg in segments:
        for para in seg.text.split("\n"):
            para = para.strip()
            if para:
                buf.append((para, seg.page, seg.section))

    if not buf:
        return chunks

    # 段落累积 + 滑动窗口
    cur_text = ""
    cur_page = buf[0][1]
    cur_section = buf[0][2]
    cur_paras: list = []

    def flush(end_idx: int):
        nonlocal cur_text, cur_page, cur_section, cur_paras
        if not cur_text.strip():
            cur_paras = []
            cur_text = ""
            return
        # 生成 chunk
        cid = f"{doc_id}#{len(chunks):04d}"
        chunks.append(Chunk(
            text=cur_text,
            doc_id=doc_id,
            chunk_id=cid,
            page=cur_page,
            section=cur_section,
            metadata={"doc_id": doc_id, "page": cur_page, "section": cur_section},
        ))
        # 滑动 overlap
        keep_paras = set()
        total_len = 0
        for i in range(len(cur_paras) - 1, -1, -1):
            total_len += len(cur_paras[i][0])
            keep_paras.add(i)
            if total_len >= overlap:
                break
        cur_paras = [cur_paras[i] for i in sorted(keep_paras)]
        cur_text = "\n".join(p[0] for p in cur_paras)
        if cur_paras:
            cur_page = cur_paras[0][1]
            cur_section = cur_paras[0][2]

    for text, page, section in buf:
        if not cur_paras:
            cur_page = page
            cur_section = section
        candidate_text = (cur_text + "\n" + text).strip()
        if _approx_tokens(candidate_text) > chunk_size and cur_text:
            flush(len(cur_paras))
        cur_paras.append((text, page, section))
        cur_text = "\n".join(p[0] for p in cur_paras)

    # 收尾
    if cur_paras:
        chunks.append(Chunk(
            text=cur_text,
            doc_id=doc_id,
            chunk_id=f"{doc_id}#{len(chunks):04d}",
            page=cur_page,
            section=cur_section,
            metadata={"doc_id": doc_id, "page": cur_page, "section": cur_section},
        ))
    return chunks
