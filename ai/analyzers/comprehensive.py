"""
综合投研报告分析器（独立编排，不与单类型分析器耦合）。

特点：
- 不区分财报/风险/估值/研报，只产出「一份」统一专业报告。
- 采用轻量 ReAct 循环，可调用白名单工具检索内部数据与外部资料
  （行情/财务/研报/公告/宏观/行业/联网搜索/RAG）。
- 通过 on_stage 回调向上层推送流程进展，便于前端 SSE 流式展示。
"""
from __future__ import annotations
import json
import re
import time
from typing import Any, Callable, Dict, List, Optional

from ai import client as llm
from ai.analyzers.base import stock_basic, save_analysis
from ai.agent import tools as tool_reg
from ai import router

# 报告模式可用的工具（白名单子集：覆盖内部数据 + 外部资料）。
# 注意：仅放「纯取数」工具；compare_stocks / suggest_investment 内部会再调一次 LLM，
# 嵌套调用易超 15s 超时，且综合报告的「撰写」应由外层 ReAct 完成，故不纳入。
COMPREHENSIVE_TOOLS = [
    "get_quote", "get_financials", "get_research_reports",
    "get_announcements", "get_industry", "get_macro",
    "rag_retrieve", "web_search",
]

MAX_STEPS = 6

REPORT_SYSTEM_PROMPT = """你是一位卖方/买方资深研究员，负责为单只 A 股产出一份**统一、专业**的个股投研报告。
你可以调用以下白名单工具收集资料（可多次调用、跨工具交叉验证）：
{tool_specs}

工作规则：
- 每轮必须二选一：**调用一个工具** 或 **输出 final_answer**。不要只输出 thought 而不行动。
- 调用工具输出：{{"thought":"推理","action":{{"tool":"工具名","args":{{...}}}}}}；
  资料齐备后输出：{{"thought":"...","final_answer":"最终报告（Markdown，中文，专业严谨）"}}。
- 只能使用上面列出的工具。股票参数可用 6 位代码或名称，系统会自动解析。
- 若某工具返回 error / degraded / 空结果，请换一个工具继续取数，不要停步；最终仍须输出 final_answer。
- 禁止无依据的数字与确定性收益承诺；明确区分「事实」与「推测」。
- 只输出 JSON，不要额外文字。

引用与来源规范（重要，按论文式处理）：
- 正文中**绝对不要**出现工具名（如 get_financials、get_quote、web_search、rag_retrieve），
  也不要出现「[来源: xxx]」这类标注。
- 正文引用一律使用**方括号数字编号**，如 [1]、[2]、[3]，置于相应句子末尾。
- 文末「## 参考资料」按编号列出每一条的**真实具体出处**（研究/新闻/公告/年报），例如：
  [1] 《XXX 公司跟踪报告：……》，诚通证券，2026-09-12
  [2] 《联瑞新材关于……的公告》，联瑞新材，2026-08-20
  [3] 《XXX 新闻标题》，证券时报，2026-09-15，https://…
  数据类来源请写具体文档或口径（如「联瑞新材 2026 年半年报」「交易所日行情数据」），
  不要写工具名或「数据库」这类笼统词。
- 编号须与正文一一对应，按首次出现顺序排列；如无对应外部资料，可依据数据文档列示。

最终报告结构（用 Markdown 二级标题，缺一不可）：
## 一、公司概览与行业地位
## 二、财务质量（营收/利润/现金流/ROE，引用具体数字）
## 三、估值水平与同业对比
## 四、近期催化与事件（来自研报/公告/联网搜索）
## 五、主要风险
## 六、结论与后续跟踪
## 参考资料
（按 [1][2][3] 编号列出上述真实来源）"""


def _tool_specs() -> str:
    lines = []
    for n in COMPREHENSIVE_TOOLS:
        spec = tool_reg.TOOLS.get(n)
        if not spec:
            continue
        ps = ", ".join(
            f"{p}({'必填' if i.get('required') else '可选'})"
            for p, i in spec["params"].items()
        )
        lines.append(f"- {n}: {spec['description']} | 参数[{ps}]")
    return "\n".join(lines)


def _parse(text: str) -> Optional[Dict[str, Any]]:
    try:
        s = text.index("{")
        e = text.rindex("}")
        return json.loads(text[s:e + 1])
    except Exception:
        return None


# 参考资料标题的多种写法（模型可能用其中任一种）
_REF_HEADING = r"#{1,6}\s*(参考资料|资料来源|参考来源|参考文献|信息来源|数据来源)"


def _build_registry(steps: List[Dict[str, Any]]) -> List[str]:
    """从已收集的工具结果中提取「真实可引用来源」，按调用顺序编号去重。"""
    entries: List[str] = []
    for s in steps:
        tool = s.get("tool")
        obs = s.get("observation") or {}
        if not isinstance(obs, dict) or obs.get("error"):
            continue
        if tool == "get_research_reports":
            for r in obs.get("reports", []) or []:
                title = r.get("报告名称") or r.get("标题") or ""
                if not title:
                    continue
                parts = [f"《{title}》"]
                if r.get("机构"):
                    parts.append(str(r["机构"]))
                if r.get("日期") or r.get("报告日期"):
                    parts.append(str(r.get("日期") or r.get("报告日期")))
                if r.get("报告PDF链接"):
                    parts.append(str(r["报告PDF链接"]))
                entries.append("，".join(parts))
        elif tool == "get_announcements":
            for a in obs.get("announcements", []) or []:
                title = (a.get("title") or "").strip()
                if not title:
                    continue
                parts = [f"《{title}》"]
                if a.get("date"):
                    parts.append(str(a["date"]))
                if a.get("url"):
                    parts.append(str(a["url"]))
                entries.append("，".join(parts))
        elif tool == "web_search":
            q = obs.get("query") or ""
            linked = False
            for r in obs.get("results", []) or []:
                title = (r.get("title") or "").strip()
                url = r.get("url") or ""
                if title and url:
                    entries.append(f"《{title}》，{url}")
                    linked = True
            if not linked and q:
                entries.append(f"联网检索（查询：{q}）")
        elif tool == "rag_retrieve":
            seen_docs = set()
            for c in obs.get("citations", []) or []:
                did = c.get("doc_id") or c.get("chunk_id")
                if did and did not in seen_docs:
                    seen_docs.add(did)
                    sec = c.get("section") or ""
                    entries.append(f"内部知识库文档（doc_id={did}"
                                   f"{('，' + sec) if sec else ''}）")
        elif tool == "get_financials":
            rd = obs.get("report_date") or (obs.get("financials") or {}).get("report_date") or ""
            entries.append(f"公司定期财务报告（报告期：{rd}）")
        elif tool == "get_quote":
            entries.append(f"交易所日行情数据（{obs.get('trade_date', '')}）")
        elif tool == "get_macro":
            entries.append("宏观指标数据库（CPI / PMI / LPR 等，最新一期）")
        elif tool == "get_industry":
            entries.append("行业归属与同业样本数据（内部行业库）")

    seen, out = set(), []
    for e in entries:
        e = e.strip()
        if e and e not in seen:
            seen.add(e)
            out.append(e)
    return out


def _format_registry(entries: List[str]) -> str:
    """把来源清单渲染成 Markdown「参考资料」段落。"""
    if not entries:
        return ("## 参考资料\n\n"
                "（本报告依据公开财务/行情数据与内部知识库生成，未引用外部文章。）")
    lines = ["## 参考资料", ""]
    lines += [f"[{i}] {e}" for i, e in enumerate(entries, 1)]
    return "\n".join(lines)


def _strip_refs(text: str) -> str:
    """去掉正文里可能自带的「参考资料 / 资料来源」章节（系统会统一附加）。"""
    m = re.search(r"\n\s*" + _REF_HEADING, text)
    return text[:m.start()].rstrip() if m else text.rstrip()


def _refs_incomplete(text: str) -> bool:
    """判断报告是否缺少/不完整的「参考资料」：正文用到的 [n] 需在参考资料中有定义。"""
    if not text:
        return True
    m = re.search(_REF_HEADING, text)
    if not m:
        return True
    body, refs = text[:m.start()], text[m.start():]
    inline = set(re.findall(r"\[(\d+)\]", body))
    if not inline:
        return False  # 正文未用编号，不强求
    defined = set(re.findall(r"\[(\d+)\]", refs))
    return not inline.issubset(defined)


def run(stock_code: str, save: bool = True,
        on_stage: Callable[[str, str, int], None] = None) -> Dict[str, Any]:
    """生成统一综合投研报告。

    on_stage(stage, label, pct) 用于进度推送；不抛异常，错误以 dict 形式返回。
    """
    info = stock_basic(stock_code)
    if not info:
        return {"error": f"找不到股票 {stock_code}"}
    code = info["stock_code"]
    name = info["short_name"]
    total_tokens = 0
    total_cost = 0.0

    if on_stage:
        on_stage("prepare", f"📊 已识别 {code} {name}，开始收集基础数据…", 8)

    messages: List[Dict[str, str]] = [
        {"role": "system",
         "content": REPORT_SYSTEM_PROMPT.format(tool_specs=_tool_specs())},
        {"role": "user",
         "content": f"请为 **{code} {name}** 生成一份统一的综合投研报告。"},
    ]

    steps: List[Dict[str, Any]] = []
    answer: Optional[str] = None
    last_model = ""

    for i in range(MAX_STEPS):
        if on_stage:
            on_stage("think",
                     f"🧠 第 {i+1}/{MAX_STEPS} 步：梳理已收集资料，决定下一步…", 30 + i * 7)
        try:
            resp = router.chat_for_task(router.TASK_COMPREHENSIVE, messages,
                                       temperature=0.2, max_tokens=4000)
            total_tokens += resp.get("total_tokens", 0)
            total_cost += llm.estimate_cost(resp.get("input_tokens", 0),
                                            resp.get("output_tokens", 0), resp.get("model", ""))
        except Exception as e:
            return {"error": f"大模型调用失败: {e}", "stock_code": code, "type": "comprehensive"}
        last_model = resp.get("model", last_model)
        text = resp.get("text", "")
        messages.append({"role": "assistant", "content": text})

        obj = _parse(text)
        if obj is None:
            messages.append({"role": "user",
                             "content": "请严格只输出 JSON：thought+action 或 thought+final_answer。"})
            continue

        if "final_answer" in obj:
            answer = obj["final_answer"]
            break

        action = obj.get("action") or {}
        tname = action.get("tool")
        targs = action.get("args") or {}
        if not tname:
            messages.append({"role": "user", "content": "未提供工具名，请重试，仅可使用白名单工具。"})
            continue
        if tname not in COMPREHENSIVE_TOOLS:
            messages.append({"role": "user",
                             "content": f"工具 `{tname}` 不在白名单内，请改用：{', '.join(COMPREHENSIVE_TOOLS)}。"})
            continue

        if on_stage:
            on_stage("tool", f"🔧 调用工具 `{tname}` 检索资料…", 30 + i * 7)
        t0 = time.time()
        # MiniMax 服务端 web_search 在服务端完成搜索+生成，耗时较长，放宽超时
        obs = tool_reg.call_tool(tname, targs, timeout=60 if tname == "web_search" else 15)
        dur = int((time.time() - t0) * 1000)
        steps.append({"tool": tname, "args": targs, "observation": obs, "duration_ms": dur})
        obs_str = json.dumps(obs, ensure_ascii=False, default=str)[:3000]
        messages.append({"role": "user", "content": f"Observation: {obs_str}"})

    # 从已收集的资料确定性地构造「参考资料」清单，保证 [n] 一定有对应条目
    registry = _build_registry(steps)

    # 需要（重新）合成的情况：没有正文，或正文用了 [n] 但没有/缺少对应参考资料
    prev_answer = answer
    need_synth = (answer is None) or _refs_incomplete(answer)
    if need_synth and steps:
        ctx = "\n\n".join(
            f"【{s['tool']}】\n{json.dumps(s['observation'], ensure_ascii=False, default=str)[:1500]}"
            for s in steps
        )
        reg_block = ("可引用来源清单（正文引用时**只能**使用下列编号）：\n"
                     + "\n".join(f"[{i}] {e}" for i, e in enumerate(registry, 1))
                     if registry else
                     "（本次未取到可结构化列出的外部来源，请如实说明数据来源，勿编造链接。）")
        synth_prompt = (
            f"你是资深研究员。请为 {code} {name} 撰写一份统一的综合投研报告（Markdown），"
            "严格按以下二级标题结构：\n"
            "## 一、公司概览与行业地位\n## 二、财务质量（引用具体数字）\n"
            "## 三、估值水平与同业对比\n## 四、近期催化与事件\n"
            "## 五、主要风险\n## 六、结论与后续跟踪\n"
            "引用规则：正文中每个结论后用方括号数字标注来源，编号**必须且只能**取自"
            "下方「可引用来源清单」（如 [1]、[3]）；不得使用清单之外的编号，不得出现工具名"
            "（如 get_financials / web_search）或「[来源: xxx]」写法。\n"
            "**不要自行撰写「参考资料」章节**，系统已在文末自动附加，重复会被删除。\n"
            "禁止确定性收益承诺；不要输出 JSON，只输出报告正文。\n\n"
            + reg_block + "\n\n以下为已收集的原始资料（供撰写参考）：\n" + ctx
        )
        for _ in range(2):  # 失败重试 1 次，规避大模型瞬时错误
            try:
                resp = router.chat_for_task(router.TASK_COMPREHENSIVE,
                                            [{"role": "user", "content": synth_prompt}],
                                            temperature=0.3, max_tokens=4000)
                total_tokens += resp.get("total_tokens", 0)
                total_cost += llm.estimate_cost(resp.get("input_tokens", 0),
                                                resp.get("output_tokens", 0), resp.get("model", ""))
                last_model = resp.get("model", last_model)
                body = _strip_refs(resp.get("text", ""))
                if body.strip():
                    answer = body.rstrip()
                    break
            except Exception:
                continue

    # 合成仍失败则退回模型此前给出的正文（若有）
    if answer is None:
        answer = prev_answer

    if answer is None:
        # 最终兜底：连正文都没有，输出已收集到的信息片段
        answer = ("（在步数内未能收敛为完整报告，以下为已收集的信息片段）\n\n" +
                  "\n".join(f"- 调用 `{s['tool']}`: "
                            f"{json.dumps(s['observation'], ensure_ascii=False, default=str)[:300]}"
                            for s in steps))
        degraded = True
    else:
        degraded = False

    # 无条件保证「参考资料」存在且能覆盖正文的 [n]：缺失则确定性补齐
    if _refs_incomplete(answer):
        answer = _strip_refs(answer).rstrip() + "\n\n" + _format_registry(registry)

    if on_stage:
        on_stage("render", "✅ 综合报告生成完成，正在整理呈现…", 100)

    from ai.structured import compute_confidence
    citations = [{"type": "doc", "ref": e[:60], "label": e, "date": ""} for e in registry]
    confidence = compute_confidence(code)

    aid = None
    if save:
        try:
            aid = save_analysis(code, "comprehensive", REPORT_SYSTEM_PROMPT,
                                answer, last_model, total_tokens, total_cost, 0,
                                citations=[c["label"] for c in citations],
                                confidence=confidence,
                                citations_obj=citations)
        except Exception:
            aid = None

    return {
        "analysis_id": aid,
        "stock_code": code,
        "type": "comprehensive",
        "response": answer,
        "model": last_model,
        "degraded": degraded,
        "steps": len(steps),
        "citations": citations,
        "score": None,
        "confidence": confidence,
        "disclaimer": True,
        "tokens": total_tokens,
        "cost_cny": total_cost,
        "duration_ms": 0,
    }
