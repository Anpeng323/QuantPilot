"""
统一 LLM 客户端：支持 Qwen / DeepSeek / MiniMax / Mock 四种模式。
- 通过环境变量 LLM_PROVIDER 切换
- 各 provider 的 API key 通过专用环境变量
"""
from __future__ import annotations
import os
import re
import time
import hashlib
import threading
from typing import List, Dict, Any
from openai import OpenAI  # 顶层 import 确保可用

PROVIDER = os.environ.get("LLM_PROVIDER", "mock").lower()

# 进度回调（线程局部，避免并发请求互相覆盖）。由上层（如流式路由）
# 设置；chat() 在模型调用前后触发，用于向前端推送「流程进展」。
_local = threading.local()


def set_progress(fn):
    """注册进度回调 fn(stage: str, label: str)。传 None 取消。"""
    _local.hook = fn


def _emit_stage(stage: str, label: str):
    hook = getattr(_local, "hook", None)
    if hook:
        try:
            hook(stage, label)
        except Exception:
            pass

# ============== Qwen ==============
QWEN_API_KEY  = os.environ.get("QWEN_API_KEY",  "")
QWEN_BASE_URL = os.environ.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL    = os.environ.get("QWEN_MODEL",    "qwen-plus")

# ============== DeepSeek ==============
DS_API_KEY    = os.environ.get("DEEPSEEK_API_KEY", "")
DS_BASE_URL   = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DS_MODEL      = os.environ.get("DEEPSEEK_MODEL",   "deepseek-chat")

# ============== MiniMax (MiniMax) ==============
MINIMAX_API_KEY  = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
MINIMAX_MODEL    = os.environ.get("MINIMAX_MODEL",    "MiniMax-M3")


def _price_per_1k(model: str, kind: str = "input") -> float:
    """估算每千 token 成本（人民币元）。用于成本审计。
    实际价格请参考官方价目表，下表为估算值（可能过期）。"""
    table = {
        "qwen-plus":               {"input": 0.004,  "output": 0.012},
        "qwen-long":               {"input": 0.0005, "output": 0.002},
        "deepseek-chat":           {"input": 0.001,  "output": 0.002},
        # MiniMax 国内版 - 大致参考价
        "MiniMax-M3":         {"input": 0.001,  "output": 0.008},
        "MiniMax-M2.7":       {"input": 0.0005, "output": 0.005},
        "MiniMax-M3.1-Flash-Preview": {"input": 0.0001, "output": 0.001},
        "mock":                    {"input": 0.0,    "output": 0.0},
    }
    return table.get(model, table["mock"]).get(kind, 0.0)


def _is_live() -> bool:
    """判断当前 provider 是否真的能调（API key 是否存在）。"""
    if PROVIDER == "qwen" and QWEN_API_KEY:
        return True
    if PROVIDER == "deepseek" and DS_API_KEY:
        return True
    if PROVIDER == "minimax" and MINIMAX_API_KEY:
        return True
    return False


def _client_and_model():
    """根据 provider 返回 (OpenAI Client, 默认 model)。"""
    from openai import OpenAI  # noqa
    if PROVIDER == "qwen":
        return OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL), QWEN_MODEL
    if PROVIDER == "deepseek":
        return OpenAI(api_key=DS_API_KEY, base_url=DS_BASE_URL), DS_MODEL
    if PROVIDER == "minimax":
        return OpenAI(api_key=MINIMAX_API_KEY, base_url=MINIMAX_BASE_URL), MINIMAX_MODEL
    return None, None


# ---------- 响应缓存（按 模型+消息指纹，TTL 24h，省 token） ----------
# 仅 live 模式生效；设置 DISABLE_LLM_CACHE=1 可关闭（便于回归比对）。
_CACHE: Dict[str, Any] = {}
_CACHE_TTL = 24 * 3600


def _cache_key(model: str, messages: List[Dict[str, str]]) -> str:
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    for m in messages:
        h.update((m.get("role", "") + "\u0001" + (m.get("content") or "")).encode("utf-8"))
    return h.hexdigest()


def _cache_get(key: str):
    if os.environ.get("DISABLE_LLM_CACHE"):
        return None
    v = _CACHE.get(key)
    if v and (time.time() - v[1]) < _CACHE_TTL:
        return v[0]
    return None


def _cache_put(key: str, val: Dict[str, Any]) -> None:
    if os.environ.get("DISABLE_LLM_CACHE"):
        return
    # 防止缓存无限增长：超 2000 条时清空旧缓存
    if len(_CACHE) > 2000:
        _CACHE.clear()
    _CACHE[key] = (val, time.time())


def _call_live(messages: List[Dict[str, str]], model: str = None,
               temperature: float = 0.3, max_tokens: int = 2048,
               timeout: int = 30) -> Dict[str, Any]:
    """真实模型调用：带超时 + 指数退避重试（2 次）；命中缓存直接返回；
    全部失败则降级返回（degraded=True），交上层 try 包裹，不抛异常。"""
    client, default_model = _client_and_model()
    model = model or default_model

    key = _cache_key(model, messages)
    cached = _cache_get(key)
    if cached is not None:
        return dict(cached, model=model, cached=True)

    last_err: Exception | None = None
    for attempt in range(3):  # 1 次 + 2 次重试
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            txt = resp.choices[0].message.content
            usage = resp.usage
            # 空内容（HTTP 200 但模型返回空串）视为失败，触发重试而非返回空
            if not (txt or "").strip():
                raise RuntimeError("LLM 返回空内容，触发重试")
            result = {
                "text": txt,
                "model": model,
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            }
            _cache_put(key, result)
            return result
        except Exception as e:  # noqa: BLE001 - 统一兜底，避免单点失败崩进程
            last_err = e
            if attempt < 2:
                time.sleep(1.5 ** attempt)  # 1.5s / 2.25s 退避，规避 MiniMax 限流

    # 重试耗尽 → 降级短文（上层按 degraded 处理）
    return {
        "text": f"（模型调用失败：{type(last_err).__name__}，已降级返回）",
        "model": model,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "degraded": True,
    }


def _call_mock(messages: List[Dict[str, str]], model: str = "mock") -> Dict[str, Any]:
    """Mock：不调网络，返回模板化说明。"""
    user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    text = (
        f"【Mock 模式】当前 LLM provider=`{PROVIDER}` 但未检测到 API key。\n"
        f"\n"
        f"**问题摘要**：{user_msg[:200]}{'…' if len(user_msg)>200 else ''}\n"
        f"\n"
        f"**说明**\n"
        f"\n"
        f"- 在 mock 模式下，我会基于 DuckDB 中的数据生成示例分析\n"
        f"- 请设置对应的 API key 环境变量后重启服务即可切真实模式：\n"
        f"\n"
        f"```bash\n"
        f"# MiniMax (MiniMax)\n"
        f"export LLM_PROVIDER=minimax\n"
        f"export MINIMAX_API_KEY=<your-key>\n"
        f"export MINIMAX_MODEL=MiniMax-M3\n"
        f"\n"
        f"# 或 Qwen\n"
        f"export LLM_PROVIDER=qwen\n"
        f"export QWEN_API_KEY=sk-xxx\n"
        f"\n"
        f"# 或 DeepSeek\n"
        f"export LLM_PROVIDER=deepseek\n"
        f"export DEEPSEEK_API_KEY=sk-xxx\n"
        f"```\n"
    )
    return {
        "text": text,
        "model": model,
        "input_tokens": max(1, len(user_msg) // 2),
        "output_tokens": max(1, len(text) // 2),
        "total_tokens": max(1, (len(user_msg) + len(text)) // 2),
    }


_THINK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.S | re.I)


def _strip_think(text: str) -> str:
    """去掉模型输出的 <think>…</think> 推理块（MiniMax/DeepSeek 等会返回）。

    这类推理块常为英文，且不是用户要看的正文，需在返回前剔除；
    若被 max_tokens 截断导致标签未闭合，则丢弃从 <think 起的所有内容。
    """
    if not text:
        return text
    t = _THINK_RE.sub("", text)
    t = re.sub(r"<think\b[^>]*>.*$", "", t, flags=re.S | re.I)
    return t.strip()


DEFAULT_SYSTEM_PROMPT = (
    "你是一位严谨的中文投研助手。请严格遵守以下规则：\n"
    "1) 无论输入使用何种语言，你的全部回答都必须使用简体中文"
    "（必要的英文专有名词、代码、缩写可保留）。\n"
    "2) 直接输出最终结论与正文，不要输出思考过程，"
    "也不要以「Let me analyze…」之类的引导语开场。\n"
    "3) 严格遵循用户要求的输出格式（Markdown 章节标题、JSON 字段等）。"
)


def chat(messages: List[Dict[str, str]], model: str = None,
         temperature: float = 0.3, max_tokens: int = 2048) -> Dict[str, Any]:
    """统一调用接口。返回 {text, model, input_tokens, output_tokens, total_tokens}。

    若调用方未自带 system 消息，则注入 DEFAULT_SYSTEM_PROMPT 强制中文输出。
    """
    msgs = list(messages or [])
    if not any((m or {}).get("role") == "system" for m in msgs):
        msgs = [{"role": "system", "content": DEFAULT_SYSTEM_PROMPT}] + msgs
    if _is_live():
        _emit_stage("llm", "🧠 大模型生成分析中（约 30–90 秒，请稍候）…")
        res = _call_live(msgs, model=model,
                         temperature=temperature, max_tokens=max_tokens)
    else:
        _emit_stage("llm", "🧠 Mock 模式生成中…")
        res = _call_mock(msgs)
    res["text"] = _strip_think(res.get("text", ""))
    _emit_stage("render", "✅ 分析完成，正在整理结果…")
    return res


def estimate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """估算一次调用的成本（人民币元）。"""
    return round(
        input_tokens / 1000 * _price_per_1k(model, "input")
      + output_tokens / 1000 * _price_per_1k(model, "output"),
        4,
    )


def provider_info() -> Dict[str, Any]:
    live = _is_live()
    info = {
        "provider": PROVIDER,
        "live": live,
    }
    if PROVIDER == "qwen":
        info["qwen_model"] = QWEN_MODEL
    if PROVIDER == "deepseek":
        info["deepseek_model"] = DS_MODEL
    if PROVIDER == "minimax":
        info["minimax_model"] = MINIMAX_MODEL
        info["minimax_base_url"] = MINIMAX_BASE_URL
    info["hint"] = (
        "Mock 模式：未检测到 API key。请设置对应环境变量后重启。"
        if not live else
        f"已启用真实 {PROVIDER} 调用。"
    )
    return info


def list_minimax_models() -> List[str]:
    """列 MiniMax 可用模型（用于测试连接）"""
    if not MINIMAX_API_KEY:
        return []
    try:
        client = OpenAI(api_key=MINIMAX_API_KEY, base_url=MINIMAX_BASE_URL, timeout=10)
        return [m.id for m in client.models.list().data]
    except Exception as e:
        return [f"ERROR: {e}"]
