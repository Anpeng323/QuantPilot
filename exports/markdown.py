"""
Markdown 导出：ai_analysis / note / watchlist → MD 文本或文件。
"""
from __future__ import annotations
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import duckdb


def _fmt_ts(ts) -> str:
    if ts is None:
        return ""
    return str(ts)[:19]


def ai_analysis_to_md(conn: duckdb.DuckDBPyConnection, analysis_id: str) -> str:
    """把 ai_analysis 行渲染为 markdown 文本"""
    row = conn.execute(
        "SELECT analysis_id, stock_code, analysis_type, prompt, response, model_used, "
        "       tokens_used, cost_cny, duration_ms, created_at "
        "FROM ai_analysis WHERE analysis_id=?",
        [analysis_id],
    ).fetchone()
    if not row:
        raise KeyError(f"找不到 ai_analysis: {analysis_id}")
    aid, code, atype, prompt, response, model, tokens, cost, dur, created = row
    parts = [
        f"# AI 分析报告 - {code} ({atype})",
        "",
        f"> **生成时间**: {_fmt_ts(created)}  ",
        f"> **模型**: {model or 'unknown'}  ",
    ]
    if tokens is not None:
        parts.append(f"> **Tokens**: {tokens}  ")
    if cost is not None:
        parts.append(f"> **成本**: ¥{cost:.4f}  ")
    if dur is not None:
        parts.append(f"> **耗时**: {dur} ms")
    parts += ["", "---", "", "## 分析结果", "", response or ""]
    return "\n".join(parts)


def note_to_md(conn: duckdb.DuckDBPyConnection, note_id: str) -> str:
    """note 表行 → markdown 文本"""
    row = conn.execute(
        "SELECT note_id, stock_code, title, content_md, tags, created_at, updated_at "
        "FROM note WHERE note_id=?",
        [note_id],
    ).fetchone()
    if not row:
        raise KeyError(f"找不到 note: {note_id}")
    nid, code, title, content, tags, created, last = row
    parts = [
        f"# {title or '无标题笔记'}",
        "",
        f"> 关联股票: {code or '(无)'}  ",
        f"> 标签: {tags or '(无)'}  ",
        f"> 创建: {_fmt_ts(created)}  ",
        f"> 更新: {_fmt_ts(last)}",
        "",
        "---",
        "",
        content or "",
    ]
    return "\n".join(parts)


def watchlist_to_md(conn: duckdb.DuckDBPyConnection, group_name: str) -> str:
    """watchlist 分组 → markdown 表格"""
    grp = conn.execute(
        "SELECT group_name, description FROM watchlist_group WHERE group_name=?",
        [group_name],
    ).fetchone()
    if not grp:
        raise KeyError(f"找不到 watchlist 分组: {group_name}")
    rows = conn.execute("""
        SELECT w.stock_code, COALESCE(s.short_name, ''), COALESCE(w.note, '')
        FROM watchlist w
        LEFT JOIN stock_basic s ON w.stock_code = s.stock_code
        WHERE w.group_name = ?
        ORDER BY w.added_at DESC
    """, [group_name]).fetchall()

    parts = [
        f"# 自选池 · {group_name}",
        "",
        f"> {grp[1] or '(无描述)'}",
        "",
        f"共 **{len(rows)}** 只标的",
        "",
        "| 代码 | 名称 | 备注 |",
        "|------|------|------|",
    ]
    for r in rows:
        parts.append(f"| {r[0]} | {r[1]} | {r[2]} |")
    return "\n".join(parts)


def ai_analysis_to_md_file(conn, analysis_id: str, out_dir: str) -> str:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    md = ai_analysis_to_md(conn, analysis_id)
    # 文件名: analysis_<id>.md
    filename = f"analysis_{analysis_id}.md"
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path
