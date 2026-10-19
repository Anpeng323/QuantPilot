#!/usr/bin/env python3
"""核心能力 HTTP 冒烟脚本（mock LLM 模式）。

对刚专业化改造的 5 大能力逐一发 HTTP 请求，断言：
  - HTTP 200
  - 专业化字段齐全：disclaimer / citations / confidence（按能力而定）
针对「能力正确性」做契约级校验；不依赖 LLM 真实输出质量。

用法：
  python3 scripts/smoke_core.py [base_url]      # 默认 http://localhost:8765
  LLM_PROVIDER=mock 推荐（确定性）
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765"
V1 = f"{BASE}/api/v1"

results: list[tuple[str, bool, str]] = []


def _post(path: str, payload: dict, stream: bool = False, timeout: int = 60):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{V1}{path}", data=data, headers={"Content-Type": "application/json"},
        method="POST",
    )
    if stream:
        return urllib.request.urlopen(req, timeout=timeout)  # 返回可读对象（SSE）
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path: str):
    with urllib.request.urlopen(f"{V1}{path}", timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, cond, detail))
    mark = "✓" if cond else "✗"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))


def assert_pro_fields(name: str, body: dict, *, need_disclaimer: bool, need_citations: bool, need_confidence: bool):
    if need_disclaimer:
        check(f"{name}: disclaimer 为真", body.get("disclaimer") is True,
              f"disclaimer={body.get('disclaimer')}")
    if need_citations:
        cit = body.get("citations")
        check(f"{name}: citations 为列表", isinstance(cit, list), f"citations={type(cit).__name__}")
    if need_confidence:
        conf = body.get("confidence")
        check(f"{name}: confidence ∈ [0,1]", isinstance(conf, (int, float)) and 0.0 <= conf <= 1.0,
              f"confidence={conf}")


# ---------------------------------------------------------------------------
# 1) AI 分析：5 类能力 + 对比 + 综合报告
# ---------------------------------------------------------------------------
def smoke_ai_analyze():
    print("\n[1] AI 分析（5 类能力 + 对比 + 综合）")
    single_types = ["financial", "risk", "valuation", "sentiment", "industry", "macro", "dcf", "profit"]
    for t in single_types:
        try:
            body = _post("/ai/analyze", {"stock_code": "000001", "type": t})
            ok = body.get("status") != "error" and "response" in body
            check(f"analyze/{t} -> 200 + response", ok, str(body)[:120])
            if ok:
                assert_pro_fields(f"analyze/{t}", body, need_disclaimer=True,
                                  need_citations=True, need_confidence=True)
        except Exception as e:
            check(f"analyze/{t} 请求异常", False, str(e)[:160])

    # 对比类（需 >=2 只股票）
    try:
        body = _post("/ai/analyze", {"type": "comparative", "stock_codes": ["000001", "600000"]})
        ok = body.get("status") != "error" and "response" in body
        detail = "" if ok else str(body)[:160]
        check("analyze/comparative(000001,600000) -> 200 + response", ok, detail)
        if ok:
            assert_pro_fields("analyze/comparative", body, need_disclaimer=True,
                              need_citations=True, need_confidence=True)
    except Exception as e:
        check("analyze/comparative 请求异常", False, str(e)[:160])

    # 综合报告（流式）
    try:
        resp = _post("/ai/analyze/comprehensive/stream",
                     {"stock_code": "000001"}, stream=True, timeout=300)
        last = None
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    last = json.loads(chunk)
                except Exception:
                    pass
        resp.close()
        ok = isinstance(last, dict) and ("response" in last or "result" in last)
        check("comprehensive/stream -> 收尾结果", ok, str(last)[:120] if last else "无数据")
        if ok and isinstance(last, dict):
            # 综合报告字段名可能嵌套在 result 内
            payload = last.get("result", last)
            assert_pro_fields("comprehensive", payload if isinstance(payload, dict) else last,
                              need_disclaimer=True, need_citations=True, need_confidence=True)
    except Exception as e:
        check("comprehensive/stream 请求异常", False, str(e)[:160])


# ---------------------------------------------------------------------------
# 2) 投研 Agent 对话
# ---------------------------------------------------------------------------
def smoke_agent():
    print("\n[2] 投研 Agent 对话")
    try:
        body = _post("/agent/ask", {"question": "平安银行最近财务表现如何？", "stock_code": "000001"})
        ok = "answer" in body and body.get("answer")
        check("agent/ask -> answer 非空", ok, str(body)[:120])
        if ok:
            assert_pro_fields("agent", body, need_disclaimer=True,
                              need_citations=True, need_confidence=True)
            check("agent: investment_view 字段存在", "investment_view" in body,
                  f"keys={list(body.keys())}")
            check("agent: suggestions 为列表", isinstance(body.get("suggestions"), list),
                  f"suggestions={type(body.get('suggestions')).__name__}")
    except Exception as e:
        check("agent/ask 请求异常", False, str(e)[:160])


# ---------------------------------------------------------------------------
# 3) RAG 知识库问答
# ---------------------------------------------------------------------------
def smoke_rag():
    print("\n[3] RAG 知识库问答")
    try:
        body = _post("/rag/query", {"question": "平安银行的竞争优势是什么？", "stock_code": "000001"})
        ok = "answer" in body
        check("rag/query -> answer 字段存在", ok, str(body)[:120])
        if ok:
            assert_pro_fields("rag/query", body, need_disclaimer=False,
                              need_citations=True, need_confidence=True)
    except Exception as e:
        check("rag/query 请求异常", False, str(e)[:160])

    try:
        body = _post("/rag/compare", {"stock_codes": ["000001", "600000"], "question": "对比两家银行"})
        ok = "answer" in body
        check("rag/compare -> answer 字段存在", ok, str(body)[:120])
    except Exception as e:
        check("rag/compare 请求异常", False, str(e)[:160])


# ---------------------------------------------------------------------------
# 4) NL 自然语言回测
# ---------------------------------------------------------------------------
def smoke_nl_backtest():
    print("\n[4] NL 自然语言回测")
    try:
        body = _post("/backtest/nl/run", {
            "natural_language": "5日均线上穿20日均线买入，下穿卖出",
            "stock_code": "000001", "start": "2023-01-01", "end": "2023-12-31",
        })
        ok = body.get("executed") is True and "metrics" in body
        check("nl/run -> executed + metrics", ok, str(body)[:160])
        if ok:
            m = body["metrics"]
            for k in ("total_return", "annual_return", "max_drawdown", "sharpe", "win_rate", "excess_return"):
                check(f"nl/run metrics.{k} 存在", k in m, f"metrics={m}")
            assert_pro_fields("nl/run", body, need_disclaimer=True,
                              need_citations=True, need_confidence=True)
    except Exception as e:
        check("nl/run 请求异常", False, str(e)[:160])


# ---------------------------------------------------------------------------
# 5) 量化选股（异步）
# ---------------------------------------------------------------------------
def smoke_screener():
    print("\n[5] 量化选股")
    try:
        # 选股引擎基于技术信号（OHLCV），基本面描述（市值/PE）不在契约内 —— 用技术描述验证主流程
        body = _post("/screener/run", {"nl": "5日均线上穿20日均线", "exclude_st": True})
        task_id = body.get("task_id")
        check("screener/run -> task_id 返回", bool(task_id), str(body)[:120])
        if not task_id:
            return
        # 轮询状态（全市场扫描较慢，最多等 ~120s）
        final = None
        for _ in range(120):
            final = _get(f"/screener/status/{task_id}")
            if final.get("status") in ("done", "error", "failed"):
                break
            time.sleep(1.0)
        ok = final.get("status") == "done" and "matches" in final
        check("screener/status -> done + matches", ok, str(final)[:160])
        if ok:
            assert_pro_fields("screener", final, need_disclaimer=True,
                              need_citations=True, need_confidence=True)
    except Exception as e:
        check("screener 请求异常", False, str(e)[:160])


def main():
    print(f"核心能力 HTTP 冒烟 @ {BASE}")
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=60) as r:
            health = json.loads(r.read().decode("utf-8"))
        check("服务健康检查 /api/health", health.get("status") == "ok" or "ok" in str(health).lower(),
              str(health)[:120])
    except Exception as e:
        check("服务不可达", False, str(e)[:160])
        _report()
        return

    smoke_ai_analyze()
    smoke_agent()
    smoke_rag()
    smoke_nl_backtest()
    smoke_screener()
    _report()


def _report():
    total = len(results)
    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 56)
    print(f"冒烟结果：{total - len(failed)}/{total} 通过")
    if failed:
        print("失败项：")
        for name, _, detail in failed:
            print(f"  - {name}  {detail}")
        sys.exit(1)
    print("全部通过 ✓")
    sys.exit(0)


if __name__ == "__main__":
    main()
