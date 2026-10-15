"""AST 白名单静态校验（Phase 3 安全门禁）。

在真正执行用户生成的策略代码之前，先做语法树级别的黑白名单检查：
- 仅允许安全的模块导入（pandas / numpy / indicators 等）与基础语法；
- 命中禁止项（危险导入、eval/exec/open、特殊属性访问等）立即拒绝，绝不执行。

这是「安全门禁」的第一道关；第二道关是 backtest.sandbox 的进程级隔离。
"""
from __future__ import annotations

import ast
from typing import List, Tuple

# 允许的模块根（白名单）。子模块（如 pandas.core）一并放行，因为 import 来自白名单根。
ALLOWED_MODULES = {
    "pandas", "numpy", "indicators", "math", "builtins", "collections",
    "datetime", "typing", "json", "re", "statistics", "itertools",
    "functools", "warnings",
}

# 禁止作为名字 / 调用出现的危险内置
FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "breakpoint", "input",
    "__import__", "exit", "quit", "globals", "locals", "vars",
    "getattr", "setattr", "delattr",
}


class SecurityError(Exception):
    """AST 校验未通过。"""


def _module_ok(module: str | None) -> bool:
    if not module:
        return False
    return module.split(".")[0] in ALLOWED_MODULES


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.errors: List[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if not _module_ok(alias.name):
                self.errors.append(
                    f"禁止导入模块 '{alias.name}'（仅允许: "
                    f"{', '.join(sorted(ALLOWED_MODULES))}）"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level != 0:
            self.errors.append("禁止使用相对导入")
        elif not _module_ok(node.module):
            self.errors.append(
                f"禁止从 '{node.module}' 导入（仅允许白名单模块）"
            )
        self.generic_visit(node)

    def visit_ImportStar(self, node: ast.ImportStar) -> None:
        self.errors.append("禁止使用 `from ... import *`")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        # 拦截 .__class__ / .__globals__ / .__subclasses__ 等沙箱逃逸入口
        if attr.startswith("__") and attr.endswith("__"):
            self.errors.append(f"禁止访问特殊属性 '.{attr}'（防沙箱逃逸）")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_NAMES:
            self.errors.append(f"禁止使用 '{node.id}'")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # 拦截通过别名调用的危险函数，如 f = eval; f(...)
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_NAMES:
            self.errors.append(f"禁止调用 '{node.func.id}'")
        self.generic_visit(node)


def validate_code_ast(code: str) -> Tuple[bool, str, List[str]]:
    """校验用户生成的策略代码。

    返回 (ok, reason, suggestions)：
    - ok=True：通过静态校验，可以进入沙箱执行；
    - ok=False：命中禁止项或语法错误，reason 为可读原因，suggestions 为修改建议。
    """
    suggestions = [
        "仅可使用 pandas / numpy / indicators 与内置数学函数。",
        "策略函数签名固定为：def strategy(df, indicators, runtime): 且必须返回布尔 Series。",
        "指标计算：indicators.get_indicator('ma', period=5).compute(df)['ma5']。",
        "买入/持有条件用 (df['close'] > ma5) 等形式；多个条件用 & 组合。",
    ]
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"代码语法错误：{e.msg}（第 {e.lineno} 行）", suggestions

    v = _Visitor()
    v.visit(tree)
    if v.errors:
        return False, "；".join(v.errors), suggestions
    return True, "", suggestions
