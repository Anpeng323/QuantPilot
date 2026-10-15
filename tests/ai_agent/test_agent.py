"""
模块4 · AI 投研智能问答 Agent 验收测试（spec 02 §6 AC2.1-2.5）。

通过 monkeypatch ai.client.chat（模拟 ReAct 决策）与工具底层函数，
在不依赖真实 MiniMax / 数据库的前提下验证：
- AC2.1 对比问题调用 compare_stocks 且答案含两股结论
- AC2.2 财报问题调用 rag_retrieve 且返回来源切片
- AC2.3 未注册工具被白名单拒绝
- AC2.4 所有答案均带 disclaimer
- AC2.5 工具异常返回降级 Observation 而非崩溃
"""
import json
import re

import pytest

from ai.agent import react
from ai.agent import tools as tool_reg
from ai import client as llm_client


@pytest.fixture
def patch_resolve(monkeypatch):
    """把名称解析打桩，避免依赖 stock_basic 表。"""
    def fake(text):
        m = re.findall(r"\d{6}", text or "")
        if m:
            return m
        d = {"茅台": "600519", "五粮液": "000858", "贵州茅台": "600519"}
        for k, v in d.items():
            if k in (text or ""):
                return [v]
        return []
    monkeypatch.setattr("ai.agent.tools.resolve_stock_codes", fake)
    monkeypatch.setattr("ai.agent.intent.resolve_stock_codes", fake)
    yield fake


class FakeChat:
    """按顺序返回脚本化的 ReAct 决策（JSON 字符串）。"""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, messages, model=None, temperature=0.3, max_tokens=2048):
        self.calls += 1
        text = self.responses[(self.calls - 1) % len(self.responses)]
        return {
            "text": text, "model": model or "MiniMax-M3",
            "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
        }


def test_ac21_compare(monkeypatch, patch_resolve):
    monkeypatch.setattr(llm_client, "chat", FakeChat([
        json.dumps({"thought": "需要对比", "action": {"tool": "compare_stocks",
                    "args": {"codes": ["600519", "000858"]}}}),
        json.dumps({"thought": "汇总", "final_answer":
                    "对比结论：茅台(600519)与五粮液(000858)估值均偏高。"}),
    ]))

    def fake_compare(stock_code="", save=True, stock_codes=None):
        return {"response": "茅台 PE 30x，五粮液 PE 20x",
                "stock_code": ",".join(stock_codes or [])}
    monkeypatch.setattr("ai.analyzers.comparative.run", fake_compare)

    res = react.ask("对比茅台和五粮液估值")
    assert res["intent"] == "compare"
    assert any(s["action"]["tool"] == "compare_stocks" for s in res["steps"])
    assert "茅台" in res["answer"] and "五粮液" in res["answer"]
    assert res["disclaimer"] is True


def test_ac22_rag_sources(monkeypatch, patch_resolve):
    monkeypatch.setattr(llm_client, "chat", FakeChat([
        json.dumps({"thought": "检索财报", "action": {"tool": "rag_retrieve",
                    "args": {"query": "贵州茅台最新财报亮点"}}}),
        json.dumps({"thought": "汇总", "final_answer":
                    "贵州茅台最新财报亮点：营收稳健增长。"}),
    ]))

    def fake_rag(question, stock_code=None, top_k=5, stock_codes=None,
                 doc_types=None, model=None):
        return {"answer": "x", "citations": [{"doc_id": "d1", "chunk_id": "c1",
                "page": 3, "text_preview": "营收增长"}]}
    monkeypatch.setattr("knowledge.rag_chain.query", fake_rag)

    res = react.ask("查贵州茅台最新财报亮点")
    assert any(s["action"]["tool"] == "rag_retrieve" for s in res["steps"])
    assert len(res["sources"]) >= 1
    assert res["sources"][0]["doc_id"] == "d1"
    assert res["disclaimer"] is True


def test_ac23_whitelist(monkeypatch, patch_resolve):
    monkeypatch.setattr(llm_client, "chat", FakeChat([
        json.dumps({"thought": "恶意", "action": {"tool": "os.system",
                    "args": {"cmd": "rm -rf /"}}}),
        json.dumps({"thought": "被拒", "final_answer": "已拒绝调用未知工具。"}),
    ]))

    res = react.ask("对比茅台和五粮液")
    assert any(s["action"]["tool"] == "os.system" for s in res["steps"])
    rej = [s for s in res["steps"] if s["action"]["tool"] == "os.system"][0]
    assert rej["observation"].get("rejected") is True
    assert "未知工具" in rej["observation"].get("error", "")
    assert res["disclaimer"] is True


def test_ac25_tool_failure_degraded(monkeypatch, patch_resolve):
    monkeypatch.setattr(llm_client, "chat", FakeChat([
        json.dumps({"thought": "查行情", "action": {"tool": "get_quote",
                    "args": {"stock_code": "600519"}}}),
        json.dumps({"thought": "汇总", "final_answer":
                    "基于行情给出观点（部分数据缺失）。"}),
    ]))

    def boom(**kwargs):
        raise RuntimeError("DB down")
    monkeypatch.setitem(tool_reg.TOOLS["get_quote"], "fn", boom)

    res = react.ask("查茅台行情")
    assert any(s["action"]["tool"] == "get_quote" for s in res["steps"])
    got = [s for s in res["steps"] if s["action"]["tool"] == "get_quote"][0]
    assert got["observation"].get("degraded") is True
    assert res["answer"]  # 未崩溃，仍有答案
    assert res["disclaimer"] is True


def test_disclaimer_always_on_chitchat(monkeypatch, patch_resolve):
    """无意图、无标的的首轮提问 -> 快速直达（不走 RAG），仍必须带 disclaimer。"""
    called = {"rag": False}

    def fake_rag(question, stock_code=None, top_k=5, **kw):
        called["rag"] = True
        return {"answer": "默认回答", "citations": []}
    monkeypatch.setattr("knowledge.rag_chain.query", fake_rag)
    monkeypatch.setattr(llm_client, "chat", FakeChat(["你好，我是 A股 投研助手。"]))

    res = react.ask("今天天气怎么样")  # unknown 意图 -> 快速直达
    assert res["disclaimer"] is True
    assert res["intent"] == "chitchat"
    assert res["answer"]
    assert res["suggestions"]
    assert called["rag"] is False  # 不再走慢且不相关的 RAG 降级
