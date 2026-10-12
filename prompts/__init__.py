"""
Prompt Template 模块。

把 ai/prompts.py 的常量模板存入 DuckDB，提供 CRUD + jinja2 渲染。
"""
from .template import PromptTemplate, seed_builtin_templates, list_builtins

__all__ = ["PromptTemplate", "seed_builtin_templates", "list_builtins"]
