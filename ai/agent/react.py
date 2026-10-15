"""
ReAct Agent 编排引擎（spec 02）。

流程：
1. 意图识别 -> 决定走 Agent 多步推理，还是走寒暄快速通道（秒级直达，不走 RAG）。
2. 主 Agent 进入 ReAct 循环：LLM 输出 Thought+Action(JSON) -> 执行白名单工具
   -> Observation 回填 -> 下一轮，直至 Final Answer 或达最大步数（默认 6）。
3. 输出 {answer, steps, sources, disclaimer, intent, degraded, model, suggestions}，
   其中 suggestions 为「追问」推荐问题，前端在输入框上方展示、可点击直发。

LLM 调用统一走 ai.client（MiniMax），满足「所有 AI 用 MiniMax」的要求。
"""
from __future__ import annotations
import ast
import json
import re
import time
from typing import Any, Dict, List, Optional

from ai import client as llm
from ai.agent import tools as tool_reg
from ai.agent import intent, compliance

MAX_STEPS = 6
REASON_MODEL = "MiniMax-M3"  # 推理用最强模型

# 无明确标的时的默认推荐问题（前端在输入框上方展示，可点击直发）
DEFAULT_SUGGESTIONS = [
    "贵州茅台最新财报亮点？",
    "对比茅台和五粮液的估值",
    "回测 600519 站上 60 日线买入",
]

SYSTEM_PROMPT = """你是一个专业的 A股 投研研究助手（ReAct Agent）。
你可以调用以下白名单工具来完成多步研究：
{tool_specs}

工作规则：
- 每轮先输出 JSON：{{"thought": "你的推理", "action": {{"tool": "工具名", "args": {{...}}}}}} 来调用一个工具；
  或在完成研究后输出 {{"thought": "...", "final_answer": "最终回答（中文，严谨客观，引用来源）"}}。
- 只能使用上面列出的工具，禁止调用白名单之外的工具（如 os.system 等）。
- 工具参数里的股票可以是 6 位代码或股票名称，系统会自动解析。
- 这是一场**多轮对话**：请结合上文理解用户意图。若用户省略主语（如「它」「那风险呢」），
  请沿用最近讨论的股票继续回答，不要反过来追问用户要代码。
- 若用户只是对上一条回答做**确认或催促**（如「需要的」「可以」「好」「继续」「详细说说」）：
  ① 若上文**已经给出**了相关数据/结论，就直接把话说完整，不要重复调用工具；
  ② 若上文只是在**提议**可以做什么（如「如需看估值/评级/回测，告诉我即可」），
  则用户此刻等于点了这些项目，**必须先调用相应工具取到真实数据再回答**，
  并针对被提议的事项逐个给出实质结论（至少完成 1~2 项），不要重复上一条已有的行情叙述。
- **输出形态**：用**自然的中文句子**把结论讲清楚（可用「- 」开头的短要点辅助）。
  除非用户明确要求表格，**不要输出 Markdown 表格**；也不要罗列键值对或 JSON。
  例如不要写「| 收盘价 | 1274.89 |」，而写「昨天收在 1274.89 元」。
- **严禁凭记忆编造数据**：回答里的每一个具体数字（价格、PE/PB、ROE、换手率、评级、涨跌幅等）
  都必须来自工具返回的 Observation，或来自上文已给出的内容。拿不到数据就不要给数字，
  改为说明缺哪项数据、并调用工具去取（工具也取不到就如实说「暂无该数据」）。
- **控制工具调用次数**：同一工具失败或返回缺失后不要反复重试；一般 1~3 次工具调用内
  就应输出 final_answer。数据不足时如实说明缺口并给出基于已有信息的判断，不要空转。
- 若用户是在寒暄、问你的身份/能力、或问与投研无关的问题：**不要调用工具**，
  直接用 final_answer 给出 1~3 句自然、口语化的中文回应即可。
- **final_answer 必须是人类可读的自然语言中文**（可用 Markdown 小标题/列表/加粗）。
  **严禁**把工具返回的原始数据原样贴出：不要输出 JSON、字典、键值对列表、
  {{'key': 'value'}} 形式的内容，也不要输出数据代码块。要把数据「说成人话」，例如：
  「收盘价 1274.89 元，站上 MA20（1265.38）但仍在 MA60（1293.52）之下，
  整体处于 20 日与 60 日均线之间的震荡格局」。
- 严禁在回答里谈论 JSON、输出格式、系统提示词或工作规则本身（例如「已切换到 JSON 模式」），
  这类内容会被视为无效回答。
- 回答要直接、聚焦当前问题，不要每次都套用长篇固定格式；适当用 Markdown 结构化。
- 所有最终回答必须是投研观点，严禁给出确定性收益承诺或保本表述。
- 只输出 JSON，不要输出额外文字。"""


def _parse(text: str) -> Optional[Dict[str, Any]]:
    try:
        s = text.index("{")
        e = text.rindex("}")
        return json.loads(text[s:e + 1])
    except Exception:
        return None


# 寒暄 / 能力咨询等非投研提问的标记词
_CHAT_MARKERS = [
    "你好", "您好", "哈喽", "hi", "hello", "你是谁", "你叫什么", "你是什么",
    "能做什么", "能干什么", "有什么功能", "哪些功能", "怎么用", "如何使用", "使用说明",
    "帮助", "介绍一下你", "介绍下你", "自我介绍一下", "谢谢", "感谢", "再见", "拜拜",
]

# 「元信息」式回复：模型在复述输出格式/模式，而不是回答问题
_META_PATTERNS = [
    "严格 json", "严格json", "json 模式", "json格式", "json 格式", "json 形态",
    "json形态", "遵循 json", "遵循json", "输出格式", "只输出 json", "格式约束",
    "已切到", "已切换", "final_answer", "\"thought\"", "compliant",
]

# 「严格遵循/遵守」需与格式类词同现才算元信息（避免误杀「严格遵循价值投资框架」）
_META_STRICT = ("严格遵循", "严格遵守", "严格按照")
_META_FORMAT_WORDS = ("json", "格式", "模式", "规则", "提示词", "指令")


def _looks_like_chitchat(question: str) -> bool:
    """是否为寒暄/能力咨询类提问（与具体投研标的无关）。"""
    q = (question or "").strip().lower()
    if not q:
        return True
    return any(m in q for m in _CHAT_MARKERS)


def _looks_like_meta(text: str) -> bool:
    """识别「我在复述规则/格式」这类无意义回复（长度较短且命中元信息词）。"""
    t = (text or "").strip()
    if not t or len(t) > 400:
        return False
    low = t.lower()
    if any(p in low for p in _META_PATTERNS):
        return True
    if any(p in low for p in _META_STRICT) and any(w in low for w in _META_FORMAT_WORDS):
        return True
    return False


# 结构化数据特征：'key': / "key": 形式的键值对；以及大段 JSON/字典块
_STRUCT_HINT = re.compile(r"['\"][A-Za-z_][A-Za-z0-9_]{1,24}['\"]\s*[:=]")
_JSONISH_BLOCK = re.compile(r"[\[{][^\[\]{}]{40,}[\]}]")

# 常见英文键名 -> 中文标签（兜底摊平时用，让要点也像人话）
_KEY_CN = {
    "stock": "标的", "code": "代码", "name": "名称", "price": "股价",
    "close": "收盘价", "trade_date": "日期", "valuation": "估值",
    "pe_ttm": "PE(TTM)", "pe": "PE", "pb": "PB", "ps": "PS",
    "total_market_cap_yi": "总市值(亿元)", "market_cap": "总市值",
    "dividend_yield": "股息率", "roe": "ROE", "revenue": "营业收入",
    "net_profit": "净利润", "growth": "增速", "view": "观点",
    "conclusion": "结论", "key_levels": "关键价位", "support": "支撑位",
    "resistance": "压力位", "ma5": "MA5", "ma10": "MA10", "ma20": "MA20",
    "ma60": "MA60", "ma120": "MA120", "rsi": "RSI", "rsi14": "RSI14",
    "macd": "MACD", "risks": "风险", "risk": "风险", "catalysts": "催化因素",
    "summary": "摘要", "signal": "信号", "metrics": "绩效", "period": "区间",
    "total_return_pct": "总收益率(%)", "max_drawdown_pct": "最大回撤(%)",
    "sharpe": "夏普", "win_rate_pct": "胜率(%)", "trade_count": "交易次数",
    "sentiment": "情绪", "rating": "评级", "target_price": "目标价",
    "volume": "成交量", "amount": "成交额", "turnover": "换手率",
}


# 回答声称「某项数据拿不到」的说法（用于判断它是否真的查过）
_MISSING_MARKERS = [
    "暂无数据", "暂无该数据", "未能拉到", "未能获取", "未能取得", "无法获取", "无法取得",
    "没有返回", "接口限制", "接口未返回", "未返回有效", "数据缺失", "拿不到", "查询不到",
    "没有找到", "未能查到", "无法查到", "暂不可用",
]


def _is_backtest_request(question: str) -> bool:
    """用户是否明确要求做策略回测。"""
    q = (question or "").lower()
    return any(m in q for m in ("回测", "backtest", "策略测试", "跑策略", "策略验证"))


def _claims_missing_data(text: str) -> bool:
    """回答是否在声称「数据取不到/缺失」（长度不限，命中标记即可）。"""
    t = (text or "").strip()
    if not t:
        return False
    return any(m in t for m in _MISSING_MARKERS)


def _looks_like_structured(text: str) -> bool:
    """判断回答是否「把原始数据当答案」：整段就是 JSON/字典/键值对堆叠。

    仅在结构化内容占绝对主体时才判定，避免误伤代码类回答与含少量示例的正常回答。
    """
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    # 代码类回答不视为结构化数据
    if "```python" in low or low.startswith("def ") or "\ndef " in low:
        return False
    t = re.sub(r"^```[a-zA-Z0-9]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    if len(t) < 20:
        return False
    if t[0] in "{[" and t[-1] in "}]":
        return True
    block = _JSONISH_BLOCK.search(t)
    if block and len(block.group(0)) * 10 >= len(t) * 6:
        return True
    hits = len(_STRUCT_HINT.findall(t))
    return hits >= 3 and hits * 14 >= len(t)


def _flatten_structured(raw: str) -> str:
    """兜底：把结构化数据摊成中文要点列表（尽量不出现裸 JSON）。"""
    s = (raw or "").strip()
    s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    obj = None
    candidates = [s] + [m.group(0) for m in _JSONISH_BLOCK.finditer(s)]
    for cand in candidates:
        for loader in (json.loads, ast.literal_eval):
            try:
                obj = loader(cand)
                break
            except Exception:
                continue
        if obj is not None:
            break
    if obj is None:
        return s

    lines: List[str] = []

    def walk(v: Any, key: str = "") -> None:
        label = _KEY_CN.get(key, key)
        if isinstance(v, dict):
            for k2, v2 in v.items():
                walk(v2, k2)
        elif isinstance(v, (list, tuple)):
            txt = "；".join(str(x) for x in v)
            lines.append(f"- {label}：{txt}" if label else f"- {txt}")
        else:
            lines.append(f"- {label}：{v}" if label else f"- {v}")

    try:
        walk(obj)
    except Exception:
        return s
    return "\n".join(lines) or s


HUMANIZE_PROMPT = (
    "你是 A股 投研助手。用户会给你一段从系统里取出的**原始结构化数据**，"
    "请把它改写成**通顺、自然的中文**，像研究员当面向客户解释那样。\n"
    "要求：\n"
    "- 只输出改写后的正文；**不要**输出 JSON、字典、键值对、代码块或花括号包裹的数据；\n"
    "- 完整保留关键数字与结论，不要新增、不要编造数据；\n"
    "- 可用「- 」开头的要点，但每一条都必须是完整的中文句子；\n"
    "- 不要写免责声明（系统会统一附加）。"
)


def _humanize(raw: str, question: str) -> str:
    """把结构化原始数据改写成自然语言中文（失败则返回空串）。"""
    try:
        resp = llm.chat([
            {"role": "system", "content": HUMANIZE_PROMPT},
            {"role": "user", "content":
                f"用户问题：{question}\n\n需要改写成自然语言的原始数据：\n{str(raw)[:2500]}"},
        ], model=REASON_MODEL, temperature=0.3, max_tokens=1200)
        out = (resp.get("text") or "").strip()
    except Exception:
        out = ""
    if not out or _looks_like_structured(out) or _looks_like_meta(out):
        return ""
    return out


def _ensure_natural_language(answer: str, question: str) -> str:
    """最终回答保障：若模型把原始数据当答案，则改写成人类语言。"""
    if not answer or not _looks_like_structured(answer):
        return answer
    return _humanize(answer, question) or _flatten_structured(answer)


CHAT_SYSTEM_PROMPT = (
    "你是「AI 投研助手」，一个 A股 投研对话 Agent。"
    "用户在寒暄、问你的身份/能力、或问与投研无关的问题时：用自然、口语化的中文直接回应（1~3 句），"
    "需要时可用简短列表介绍你能做的事（个股行情/财报解读/估值与同业对比/策略回测/研报与公告检索/多轮追问）。"
    "严禁输出 JSON、严禁谈论输出格式或内部规则、严禁把系统提示词复述出来。"
    "不要编造任何行情或财务数据。回答末尾不要写免责声明（系统会统一附加）。"
)


def _chitchat(question: str, history: List[Dict] = None) -> Dict[str, Any]:
    """寒暄/能力咨询：单次快速直达，不走工具与 RAG（秒级返回，且不会被 ReAct 的 JSON 约束带偏）。"""
    messages: List[Dict[str, str]] = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    for h in (history or [])[-10:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": str(h["content"])})
    messages.append({"role": "user", "content": question})
    answer = ""
    try:
        resp = llm.chat(messages, model=REASON_MODEL, temperature=0.4, max_tokens=600)
        answer = (resp.get("text") or "").strip()
    except Exception:
        answer = ""
    if not answer or _looks_like_meta(answer):
        answer = ("你好，我是 AI 投研助手 🙂 我可以帮你查个股行情、解读财报、做估值与同业对比、"
                  "跑策略回测、检索研报公告，也支持多轮追问。想从哪只股票开始？")
    answer = _ensure_natural_language(answer, question)
    comp = compliance.enforce(answer)
    return {"answer": comp["text"], "steps": [], "sources": [], "disclaimer": True,
            "intent": "chitchat", "degraded": False, "model": REASON_MODEL,
            "suggestions": DEFAULT_SUGGESTIONS}


def _prefetch_data(codes: List[str]) -> List[Dict[str, Any]]:
    """确定性取一次真实行情快照（最新收盘/均线等）。

    用途：① 进入推理前预先注入，避免模型凭记忆编造价格；
         ② 全程没调工具时兜底，保证合成回答有据可依。
    """
    code = (codes or [None])[0]
    if not code:
        return []
    out: List[Dict[str, Any]] = []
    for tool, args in (("get_quote", {"stock_code": code}),):
        t0 = time.time()
        try:
            obs = tool_reg.call_tool(tool, args)
        except Exception:
            continue
        if isinstance(obs, dict) and obs.get("error"):
            continue
        out.append({"thought": "系统预取行情快照（真实数据）",
                    "action": {"tool": tool, "args": args},
                    "observation": obs,
                    "duration_ms": int((time.time() - t0) * 1000)})
    return out


def _synthesize(question: str, history: List[Dict], steps: List[Dict],
                codes: List[str]) -> str:
    """基于已获取的工具结果强制合成最终回答。

    使用独立的干净上下文，避免把「请只输出 JSON」之类的格式提示当成答案返回。
    """
    if steps:
        digest = "\n".join(
            f"[{i + 1}] {s['action']['tool']}({json.dumps(s['action']['args'], ensure_ascii=False, default=str)})"
            f" -> {json.dumps(s['observation'], ensure_ascii=False, default=str)[:1500]}"
            for i, s in enumerate(steps))
        prompt = (f"用户问题：{question}\n\n"
                  + (f"涉及股票：{', '.join(codes)}\n" if codes else "")
                  +                   f"以下是工具已返回的数据（可能不完整）：\n{digest}\n\n"
                  "请直接用**自然通顺的中文**给出最终回答（像研究员当面解释那样，"
                  "可用少量「- 」短要点），不要输出 JSON、键值对或 Markdown 表格。"
                  "只能使用上面数据里的数字；若个别数据缺失，请如实说明缺口，不要编造。")
    else:
        prompt = f"用户问题：{question}\n\n请直接给出回答。"
    messages: List[Dict[str, str]] = [{"role": "system", "content":
        "你是专业的 A股 投研助手。请直接用中文回答用户，可用 Markdown 结构化。"
        "严禁输出 JSON、严禁谈论输出格式或模式、严禁复述规则。"
        "若问题与投研无关，友好简短回应并自然引导到投研话题。"
        "所有回答均为研究观点，不得承诺收益或保本。"}]
    for h in (history or [])[-10:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": str(h["content"])})
    messages.append({"role": "user", "content": prompt})
    try:
        resp = llm.chat(messages, model=REASON_MODEL, temperature=0.3, max_tokens=3000)
        return (resp.get("text") or "").strip()
    except Exception:
        return ""


def _fail(msg: str) -> Dict[str, Any]:
    return {"answer": msg, "steps": [], "sources": [], "disclaimer": True,
            "intent": "unknown", "degraded": True, "model": "none",
            "suggestions": DEFAULT_SUGGESTIONS}


def ask(question: str, stock_code: str = None, history: List[Dict] = None,
        max_steps: int = MAX_STEPS) -> Dict[str, Any]:
    intent_name, codes = intent.classify(question)
    if stock_code and stock_code not in (codes or []):
        codes = [stock_code] + (codes or [])
    codes = codes or []

    # 上文最近讨论的标的（用于多轮追问省略主语的情况）
    history_codes = _suggest_codes([], history)

    # 寒暄 / 能力咨询 / 无投研线索的闲聊：走快速直达通道。
    # 不套 ReAct 的 JSON 约束，避免出现「已切到严格 JSON 模式」这类怪回复。
    if not codes and intent_name == "unknown" and (
            _looks_like_chitchat(question) or not history_codes):
        return _chitchat(question, history)

    # 多轮追问省略主语（如「那它的风险呢」）：沿用上文股票，继续走 Agent
    if not codes and history_codes:
        codes = history_codes

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT.format(tool_specs=tool_reg.tool_specs_text())},
    ]
    # 历史对话置于当前问题之前（多轮上下文）
    if history:
        for h in history[-20:]:
            if isinstance(h, dict) and h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": str(h["content"])})
    messages.append({"role": "user", "content": f"用户问题：{question}"
                     + (f"\n（已知股票代码：{', '.join(codes)}）" if codes else "")})

    steps: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    cards: List[Dict[str, Any]] = []
    answer: Optional[str] = None
    degraded = False
    nudged = 0      # 是否已提醒「先取数再回答」
    bt_nudged = 0   # 是否已提醒「必须真的跑回测」

    # 涉及个股：先把真实行情快照喂给模型（防止凭记忆编造价格/均线）
    if codes:
        for pre in _prefetch_data(codes):
            steps.append(pre)
            messages.append({"role": "user", "content":
                             "Observation（系统预取的实时行情，真实数据）: "
                             f"{json.dumps(pre['observation'], ensure_ascii=False, default=str)[:2000]}"})
    primed = len(steps)   # 系统预取的步数（区别于模型主动调用的步数）

    for _ in range(max_steps):
        try:
            resp = llm.chat(messages, model=REASON_MODEL, temperature=0.2, max_tokens=3000)
        except Exception:
            return _fail("大模型调用失败，请稍后重试")
        text = resp.get("text", "")
        obj = _parse(text)
        messages.append({"role": "assistant", "content": text})

        if obj is None:
            # 未返回合法 JSON：无标的的纯对话式回复可直接采信；否则最后一步交给合成通道
            if not _looks_like_meta(text) and not steps and not codes:
                answer = text
                break
            if len(steps) >= max_steps - 1:
                break  # answer 仍为 None -> 走下方强制合成
            messages.append({"role": "user", "content":
                             "请严格只输出 JSON（thought+action 或 thought+final_answer），"
                             "不要寒暄、不要解释输出格式。"})
            continue

        if "final_answer" in obj:
            fa = str(obj.get("final_answer") or "").strip()
            if fa and not _looks_like_meta(fa):
                # ① 涉及个股却一次工具都没调 -> 不许凭记忆作答
                # ② 自己在回答里说「数据拿不到」，但一次工具都没调 -> 必须先真查一次
                # ③ 用户明确要求回测却没跑 -> 必须真的跑一次
                if (not bt_nudged and _is_backtest_request(question)
                        and not any(s["action"]["tool"] == "run_backtest" for s in steps)):
                    bt_nudged = 1
                    messages.append({"role": "user", "content":
                                     "用户明确要求做策略回测，但你还未调用 run_backtest。"
                                     "请立刻调用 run_backtest，参数如 "
                                     "{\"stock_code\": \"600519\", \"signal\": \"close > ma60\", "
                                     "\"start\": \"2024-01-01\", \"end\": \"<今天>\"}，"
                                     "signal 必须是比较表达式（如 close > ma60 / ma5 > ma20 / "
                                     "close > ma60 AND rsi14 < 40），不要传策略名，也不要用括号。"
                                     "拿到回测结果后，用自然语言把收益、回撤、胜率等讲清楚。"})
                    continue

                if codes and not nudged and len(steps) <= primed:
                    nudged = 1
                    messages.append({"role": "user", "content":
                                     "你还没有主动调用任何工具。本题涉及个股，禁止凭记忆给出任何数字"
                                     "（PE/PB/ROE/评级/目标价等），也不要未经尝试就说数据不可得。"
                                     "请先实际调用工具取数：get_quote（行情/均线）、get_financials（估值/财务）、"
                                     "suggest_investment（估值/风险综合）、get_research_reports（研报评级）、"
                                     "get_announcements（公告）、compare_stocks（对比）、run_backtest（回测）等，"
                                     "再输出 final_answer。若工具返回「无数据」，就如实说明缺哪项数据，"
                                     "并只就已取到的真实数据作答。"
                                     "请立刻继续处理用户的原问题（该跑回测就跑回测、该查估值就查估值），"
                                     "不要回复「已收到」「已遵循格式」这类与问题无关的话。"})
                    continue
                answer = fa
                break
            if len(steps) >= max_steps - 1:
                break
            messages.append({"role": "user", "content":
                             "请针对用户问题给出实质回答（不要复述规则或输出格式），"
                             "需要数据时先调用工具获取。"})
            continue

        action = obj.get("action") or {}
        tname = action.get("tool")
        targs = action.get("args") or {}
        if not tname:
            messages.append({"role": "user", "content":
                             "未提供工具名，请重试，仅可使用白名单工具。"})
            continue

        t0 = time.time()
        obs = tool_reg.call_tool(tname, targs)
        dur = int((time.time() - t0) * 1000)

        if tname in ("rag_retrieve", "summarize_filing") and isinstance(obs, dict):
            for c in obs.get("citations", []):
                sources.append({"doc_id": c.get("doc_id"), "chunk_id": c.get("chunk_id"),
                               "page": c.get("page"), "text_preview": c.get("text_preview")})

        # 收集可渲染的结构化卡片（如回测结果）
        if isinstance(obs, dict) and obs.get("card"):
            cards.append(obs["card"])

        steps.append({
            "thought": obj.get("thought", ""),
            "action": {"tool": tname, "args": targs},
            "observation": obs,
            "duration_ms": dur,
        })
        obs_str = json.dumps(obs, ensure_ascii=False, default=str)[:2000]
        messages.append({"role": "user", "content":
                         f"Observation: {obs_str}\n"
                         "（以上为原始数据，回答时请转述为自然语言，不要原样贴出）"})

    if answer is None:
        # 未收敛 -> 用独立干净上下文强制合成最终回答（不把格式提示当答案，也不返回「请拆分问题」）
        degraded = bool(steps)
        # 涉及个股但全程没调工具 -> 确定性补取行情/财务，避免合成时无据可依
        if codes and not steps:
            steps.extend(_prefetch_data(codes))
            degraded = bool(steps)
        answer = _synthesize(question, history, steps, codes)
        if not answer:
            answer = ("该问题涉及的数据暂不完整，以下是已获取的初步信息：\n"
                      + "\n".join(
                          f"- {s['action']['tool']}: "
                          f"{json.dumps(s['observation'], ensure_ascii=False, default=str)[:300]}"
                          for s in steps)) if steps else "抱歉，我暂时没能给出回答，请换个说法再试一次。"

    # 最后一道保障：把「原始数据当答案」的回复改写成人类语言
    answer = _ensure_natural_language(answer or "", question)
    comp = compliance.enforce(answer or "")
    return {
        "answer": comp["text"],
        "steps": steps,
        "sources": sources,
        "cards": cards,
        "disclaimer": True,
        "intent": intent_name,
        "degraded": degraded,
        "model": REASON_MODEL,
        "suggestions": _suggest_followups(_suggest_codes(codes, history), intent_name),
    }


def _suggest_codes(codes: List[str], history: List[Dict]) -> List[str]:
    """追问建议用的标的：当前问题解析不到时，沿用上文最近一次提到的股票。"""
    if codes:
        return codes
    for h in reversed(history or []):
        if isinstance(h, dict) and h.get("role") == "user" and h.get("content"):
            cs = intent.resolve_stock_codes(str(h["content"]))
            if cs:
                return cs
    return []


def _suggest_followups(codes: List[str], intent_name: str) -> List[str]:
    """按当前对话主体生成「追问」建议，前端在输入框上方展示、点击直发。"""
    if codes:
        from ai.agent.stocks import code_to_name
        label = code_to_name(codes[0]) or codes[0]
        return [
            f"{label} 最新财报亮点？",
            f"{label} 的主要风险有哪些？",
            f"{label} 与同行业公司估值对比",
        ]
    return list(DEFAULT_SUGGESTIONS)
