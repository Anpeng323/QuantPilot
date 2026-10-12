"""
TDD: AI 模型路由
"""
import pytest


class TestRouteModel:
    def test_deep_tasks_use_big_model(self):
        from ai.router import route_model
        for task in ["financial_summary", "valuation", "dcf_input", "industry_analysis"]:
            m = route_model(task)
            assert m == "MiniMax-M3", f"{task} 应该用大模型，实际={m}"

    def test_light_tasks_use_small_model(self):
        from ai.router import route_model
        for task in ["sentiment", "extraction", "classification", "summary"]:
            m = route_model(task)
            assert "Flash" in m or "mini" in m.lower(), f"{task} 应该用轻量模型，实际={m}"

    def test_rag_uses_long_context(self):
        from ai.router import route_model
        m = route_model("rag_qa")
        assert m == "qwen-long", f"rag_qa 应该用 qwen-long，实际={m}"

    def test_unknown_task_returns_default(self):
        from ai.router import route_model
        m = route_model("some_random_unknown_task")
        assert m == "MiniMax-M3" or "Flash" in m


class TestRouterConfig:
    def test_default_config(self):
        from ai.router import DEFAULT_CONFIG
        assert "financial_summary" in DEFAULT_CONFIG
        assert "rag_qa" in DEFAULT_CONFIG

    def test_route_with_custom_config(self, monkeypatch):
        from ai import router
        custom = {"financial_summary": "mock"}
        router.set_config(custom)
        assert router.route_model("financial_summary") == "mock"
        router.reset_config()


class TestRouterIntegration:
    def test_chat_for_task_passes_correct_model(self, monkeypatch):
        """chat_for_task 把路由出来的 model 透传给底层 chat()"""
        from ai import router, client as llm_client

        called = {}
        def fake_chat(messages, model=None, **kwargs):
            called["model"] = model
            return {"text": "OK", "model": model, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        monkeypatch.setattr(llm_client, "chat", fake_chat)

        result = router.chat_for_task(
            task_type="financial_summary",
            messages=[{"role": "user", "content": "hi"}],
        )
        expected = router.route_model("financial_summary")
        assert called["model"] == expected
        assert result["model"] == expected
