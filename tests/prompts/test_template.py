"""
TDD: PromptTemplate（DB + 内存 CRUD）
"""
import pytest


class TestPromptTemplate:
    """测试 PromptTemplate CRUD"""

    def test_create_and_get(self, empty_duckdb_conn):
        from prompts.template import PromptTemplate

        t = PromptTemplate.create(
            conn=empty_duckdb_conn,
            template_id="t1",
            name="财务分析",
            category="financial",
            body="你是分析师。请基于 {{ context }} 总结 {{ stock_code }}。",
            variables=["context", "stock_code"],
            is_builtin=False,
        )
        assert t.template_id == "t1"
        assert t.name == "财务分析"
        assert t.variables == ["context", "stock_code"]
        assert t.is_builtin is False

        got = PromptTemplate.get(empty_duckdb_conn, "t1")
        assert got is not None
        assert got.body == "你是分析师。请基于 {{ context }} 总结 {{ stock_code }}。"

    def test_create_with_default_id(self, empty_duckdb_conn):
        from prompts.template import PromptTemplate

        t = PromptTemplate.create(
            conn=empty_duckdb_conn,
            name="风险扫描",
            category="risk",
            body="内容",
        )
        assert t.template_id is not None
        assert len(t.template_id) > 0

    def test_duplicate_id_raises(self, empty_duckdb_conn):
        from prompts.template import PromptTemplate
        PromptTemplate.create(conn=empty_duckdb_conn, template_id="dup", name="A", body="aaa")
        with pytest.raises(ValueError, match="已存在"):
            PromptTemplate.create(conn=empty_duckdb_conn, template_id="dup", name="B", body="bbb")

    def test_list_by_category(self, empty_duckdb_conn):
        from prompts.template import PromptTemplate

        PromptTemplate.create(conn=empty_duckdb_conn, template_id="f1", name="F1", category="financial", body="x")
        PromptTemplate.create(conn=empty_duckdb_conn, template_id="r1", name="R1", category="risk",      body="y")
        PromptTemplate.create(conn=empty_duckdb_conn, template_id="f2", name="F2", category="financial", body="z")

        fin = PromptTemplate.list_by_category(empty_duckdb_conn, "financial")
        assert len(fin) == 2
        assert {t.template_id for t in fin} == {"f1", "f2"}

        risk = PromptTemplate.list_by_category(empty_duckdb_conn, "risk")
        assert len(risk) == 1
        assert risk[0].template_id == "r1"

    def test_list_all(self, empty_duckdb_conn):
        from prompts.template import PromptTemplate
        PromptTemplate.create(conn=empty_duckdb_conn, template_id="a", name="A", category="x", body="")
        PromptTemplate.create(conn=empty_duckdb_conn, template_id="b", name="B", category="y", body="")
        all_t = PromptTemplate.list_all(empty_duckdb_conn)
        assert len(all_t) == 2

    def test_update_body(self, empty_duckdb_conn):
        from prompts.template import PromptTemplate

        PromptTemplate.create(conn=empty_duckdb_conn, template_id="u1", name="U", category="x", body="旧")
        updated = PromptTemplate.update(empty_duckdb_conn, "u1", body="新内容")
        assert updated.body == "新内容"

        got = PromptTemplate.get(empty_duckdb_conn, "u1")
        assert got.body == "新内容"

    def test_update_nonexistent_raises(self, empty_duckdb_conn):
        from prompts.template import PromptTemplate
        with pytest.raises(KeyError):
            PromptTemplate.update(empty_duckdb_conn, "nope", body="x")

    def test_delete_user_template(self, empty_duckdb_conn):
        from prompts.template import PromptTemplate

        PromptTemplate.create(conn=empty_duckdb_conn, template_id="d1", name="D", category="x", body="y")
        PromptTemplate.delete(empty_duckdb_conn, "d1")
        assert PromptTemplate.get(empty_duckdb_conn, "d1") is None

    def test_delete_builtin_raises(self, empty_duckdb_conn):
        from prompts.template import PromptTemplate

        PromptTemplate.create(
            conn=empty_duckdb_conn, template_id="b1", name="内置",
            category="financial", body="...", is_builtin=True,
        )
        with pytest.raises(PermissionError, match="内置"):
            PromptTemplate.delete(empty_duckdb_conn, "b1")

    def test_update_builtin_raises(self, empty_duckdb_conn):
        from prompts.template import PromptTemplate

        PromptTemplate.create(
            conn=empty_duckdb_conn, template_id="b1", name="内置",
            category="financial", body="...", is_builtin=True,
        )
        with pytest.raises(PermissionError, match="内置"):
            PromptTemplate.update(empty_duckdb_conn, "b1", body="hack")

    def test_render_template(self, empty_duckdb_conn):
        from prompts.template import PromptTemplate

        t = PromptTemplate.create(
            conn=empty_duckdb_conn, template_id="r1", name="R",
            category="x",
            body="你是分析师。基于 {{ context }} 总结 {{ stock_code }} 表现。",
        )
        rendered = t.render({"context": "Q1 营收 100 亿", "stock_code": "000001"})
        assert "000001" in rendered
        assert "Q1 营收 100 亿" in rendered


class TestPromptSeedBuiltin:
    def test_seed_creates_builtin_templates(self, empty_duckdb_conn):
        from prompts.template import seed_builtin_templates, list_builtins

        n1 = seed_builtin_templates(empty_duckdb_conn)
        assert n1 >= 5, f"至少应有 5 个内置模板，实际={n1}"

        builtins = list_builtins(empty_duckdb_conn)
        ids = {t.template_id for t in builtins}
        assert "financial_profit" in ids
        assert "risk" in ids
        assert "valuation" in ids
        assert "sentiment" in ids
        assert "rag" in ids

        n2 = seed_builtin_templates(empty_duckdb_conn)
        assert n2 == 0, "重复 seed 不应新增"

        builtins2 = list_builtins(empty_duckdb_conn)
        assert len(builtins2) == len(builtins)
