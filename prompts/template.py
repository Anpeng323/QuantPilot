"""
PromptTemplate：把提示词模板持久化到 DuckDB。
"""
from __future__ import annotations
import json
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict

import duckdb
from jinja2 import Template


@dataclass
class PromptTemplate:
    template_id: str
    name: str
    category: str
    body: str
    variables: List[str] = field(default_factory=list)
    is_builtin: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def render(self, context: Dict[str, Any]) -> str:
        return Template(self.body).render(**context)

    @staticmethod
    def create(
        conn: duckdb.DuckDBPyConnection,
        template_id: Optional[str] = None,
        name: str = "",
        category: str = "custom",
        body: str = "",
        variables: Optional[List[str]] = None,
        is_builtin: bool = False,
    ) -> "PromptTemplate":
        tid = template_id or uuid.uuid4().hex[:12]
        existing = conn.execute(
            "SELECT 1 FROM prompt_template WHERE template_id=?", [tid]
        ).fetchone()
        if existing:
            raise ValueError(f"模板 id 已存在: {tid}")

        vars_json = json.dumps(variables or [], ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO prompt_template
                (template_id, name, category, body, variables, is_builtin)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [tid, name, category, body, vars_json, is_builtin],
        )
        return PromptTemplate.get(conn, tid)

    @staticmethod
    def get(conn: duckdb.DuckDBPyConnection, template_id: str) -> Optional["PromptTemplate"]:
        row = conn.execute(
            "SELECT template_id, name, category, body, variables, is_builtin, created_at, updated_at "
            "FROM prompt_template WHERE template_id=?",
            [template_id],
        ).fetchone()
        if not row:
            return None
        return PromptTemplate(
            template_id=row[0],
            name=row[1] or "",
            category=row[2] or "",
            body=row[3] or "",
            variables=json.loads(row[4]) if row[4] else [],
            is_builtin=bool(row[5]),
            created_at=str(row[6]) if row[6] else None,
            updated_at=str(row[7]) if row[7] else None,
        )

    @staticmethod
    def list_all(conn: duckdb.DuckDBPyConnection) -> List["PromptTemplate"]:
        rows = conn.execute(
            "SELECT template_id FROM prompt_template ORDER BY is_builtin DESC, category, name"
        ).fetchall()
        return [PromptTemplate.get(conn, r[0]) for r in rows]

    @staticmethod
    def list_by_category(conn: duckdb.DuckDBPyConnection, category: str) -> List["PromptTemplate"]:
        rows = conn.execute(
            "SELECT template_id FROM prompt_template WHERE category=? ORDER BY name",
            [category],
        ).fetchall()
        return [PromptTemplate.get(conn, r[0]) for r in rows]

    @staticmethod
    def list_builtins(conn: duckdb.DuckDBPyConnection) -> List["PromptTemplate"]:
        rows = conn.execute(
            "SELECT template_id FROM prompt_template WHERE is_builtin=true ORDER BY category, name"
        ).fetchall()
        return [PromptTemplate.get(conn, r[0]) for r in rows]

    @staticmethod
    def update(
        conn: duckdb.DuckDBPyConnection,
        template_id: str,
        name: Optional[str] = None,
        category: Optional[str] = None,
        body: Optional[str] = None,
        variables: Optional[List[str]] = None,
    ) -> "PromptTemplate":
        existing = PromptTemplate.get(conn, template_id)
        if not existing:
            raise KeyError(f"模板不存在: {template_id}")
        if existing.is_builtin:
            raise PermissionError("内置模板不可修改")

        updates = []
        params: List[Any] = []
        if name is not None:
            updates.append("name=?"); params.append(name)
        if category is not None:
            updates.append("category=?"); params.append(category)
        if body is not None:
            updates.append("body=?"); params.append(body)
        if variables is not None:
            updates.append("variables=?"); params.append(json.dumps(variables, ensure_ascii=False))

        if not updates:
            return existing

        updates.append("updated_at=current_timestamp")
        params.append(template_id)
        conn.execute(
            f"UPDATE prompt_template SET {', '.join(updates)} WHERE template_id=?",
            params,
        )
        return PromptTemplate.get(conn, template_id)

    @staticmethod
    def delete(conn: duckdb.DuckDBPyConnection, template_id: str) -> bool:
        existing = PromptTemplate.get(conn, template_id)
        if not existing:
            return False
        if existing.is_builtin:
            raise PermissionError("内置模板不可删除")
        conn.execute("DELETE FROM prompt_template WHERE template_id=?", [template_id])
        return True


_BUILTIN_TEMPLATES = [
    {"template_id": "financial_profit", "name": "财报解读", "category": "financial",
     "variables": ["stock_code", "short_name", "financial_context"]},
    {"template_id": "risk",             "name": "风险扫描", "category": "risk",
     "variables": ["stock_code", "short_name", "risk_context"]},
    {"template_id": "valuation",        "name": "估值测算", "category": "valuation",
     "variables": ["valuation_context"]},
    {"template_id": "sentiment",        "name": "研报观点汇总", "category": "sentiment",
     "variables": ["reports_context"]},
    {"template_id": "rag",              "name": "RAG 问答", "category": "rag",
     "variables": ["question", "context"]},
    {"template_id": "review",           "name": "交易复盘", "category": "review",
     "variables": ["trades", "user_note"]},
]


def seed_builtin_templates(conn: duckdb.DuckDBPyConnection) -> int:
    from ai import prompts as ai_prompts
    name_to_const = {
        "financial_profit": ai_prompts.PROMPT_FINANCIAL,
        "risk":             ai_prompts.PROMPT_RISK,
        "valuation":        ai_prompts.PROMPT_VALUATION,
        "sentiment":        ai_prompts.PROMPT_SENTIMENT,
        "rag":              ai_prompts.PROMPT_RAG,
        "review":           ai_prompts.PROMPT_REVIEW,
    }

    n_inserted = 0
    for tmpl in _BUILTIN_TEMPLATES:
        if PromptTemplate.get(conn, tmpl["template_id"]):
            continue
        body = name_to_const.get(tmpl["template_id"], "")
        PromptTemplate.create(
            conn=conn,
            template_id=tmpl["template_id"],
            name=tmpl["name"],
            category=tmpl["category"],
            body=body,
            variables=tmpl["variables"],
            is_builtin=True,
        )
        n_inserted += 1
    return n_inserted


# 兼容别名
list_builtins = PromptTemplate.list_builtins
