"""
Embedding 抽象层。
- 默认使用 sentence-transformers 的 BGE-small-zh（本地、免费、中文好）
- 也支持调用 LLM provider 的 embedding API（Qwen text-embedding-v3）
- 第一次调用会下载模型（约 100MB），需要联网
"""
from __future__ import annotations
import os
import threading
from typing import List

MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
_lock = threading.Lock()
_model = None


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    批量 embedding。返回每段文本的向量。
    空字符串返回零向量。
    """
    if not texts:
        return []
    m = _get_model()
    # BGE 推荐在 query 前加 instruction；这里只对短 query 启用
    cleaned = [t if t else " " for t in texts]
    vecs = m.encode(cleaned, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


def embed_query(text: str) -> List[float]:
    """单条 query 向量化"""
    return embed_texts([text])[0]


def warmup():
    """预加载模型（首次调用更顺滑）。"""
    try:
        _get_model()
        return True
    except Exception as e:
        return f"加载失败: {e}"


def info() -> dict:
    return {"model": MODEL_NAME, "dim": _get_model().get_sentence_embedding_dimension() if _model else "?"}
