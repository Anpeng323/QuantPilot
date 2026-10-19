"""研报观点汇总"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from jinja2 import Template
from etl.db import get_conn
from ai.prompts import PROMPT_SENTIMENT
from ai.analyzers.base import stock_basic
from ai import router
from ai.structured import run_simple


def run(stock_code: str, save: bool = True) -> dict:
    info = stock_basic(stock_code)
    if not info:
        return {"error": f"找不到股票 {stock_code}"}
    conn = get_conn()
    docs = conn.execute("""
        SELECT doc_id, title, publish_date, file_path
        FROM doc WHERE stock_code=? AND doc_type IN ('report', 'research')
        ORDER BY publish_date DESC LIMIT 10
    """, [stock_code]).fetchall()

    if not docs:
        ctx = f"## 股票\n代码: {stock_code} {info.get('short_name','')}\n\nv1 暂未找到该股的研报数据。请先上传研报 PDF 到 RAG 知识库（doc_type='report'，关联 stock_code={stock_code}）。"
    else:
        ctx = f"## 股票\n代码: {stock_code} {info.get('short_name','')}\n\n## 已入库研报（{len(docs)} 份）\n"
        for d in docs:
            ctx += f"- {d[2]}  {d[1]}  ({d[0]})\n"
        ctx += "\n（v1 仅展示研报列表，详细观点合并由 RAG 提问完成）"

    tpl = Template(PROMPT_SENTIMENT)
    prompt = tpl.render(reports_context=ctx)
    doc_citations = [
        {"type": "doc", "ref": d[0], "label": f"研报：{d[1]}", "date": str(d[2] or "")}
        for d in docs
    ] if docs else None
    return run_simple(router.TASK_SENTIMENT, stock_code, prompt, save,
                      temperature=0.3, max_tokens=1200, atype="sentiment",
                      doc_citations=doc_citations)
