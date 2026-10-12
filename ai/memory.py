"""
用户偏好记忆（简单 KV 存储）。
- 投资风格（价值/成长/趋势/量化）
- 关注行业
- 风险偏好（保守/平衡/激进）
- 偏好模型
"""
import json
from pathlib import Path
from threading import Lock

MEMORY_PATH = Path(__file__).resolve().parent.parent / "data" / "user_memory.json"
_lock = Lock()

DEFAULT_MEMORY = {
    "style": "",                  # "价值" / "成长" / "趋势" / "量化"
    "favorite_industries": [],     # ["半导体", "新能源"]
    "risk_preference": "平衡",     # "保守" / "平衡" / "激进"
    "favorite_llm": "qwen-plus",
    "notes": "",                  # 自由文本偏好
}


def load_memory() -> dict:
    if MEMORY_PATH.exists():
        try:
            return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return dict(DEFAULT_MEMORY)


def save_memory(m: dict) -> None:
    with _lock:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_PATH.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def update_memory(updates: dict) -> dict:
    m = load_memory()
    m.update(updates)
    save_memory(m)
    return m
