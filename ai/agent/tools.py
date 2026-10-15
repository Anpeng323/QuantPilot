"""
统一工具注册表（白名单）：Agent 仅能调用此处登记的工具。

工具列表（spec 02 §3.3）：
- get_quote          行情/技术指标
- get_financials     财务三表/估值
- compare_stocks     多股对比
- rag_retrieve       知识库检索
- summarize_filing   长文档摘要（基于 RAG 上下文）
- run_backtest       规则回测（Phase 1）
- suggest_investment 投资建议（Phase 4 接入）

每条工具：
- description : 给 LLM 看的中文说明
- params      : 参数 schema（required / 可选 + 类型说明）
- fn          : 真实执行函数（接收解析后的 kwargs）

调用规则（spec §3.4）：白名单、参数校验、超时 15s 重试 1 次、失败降级。
"""
from __future__ import annotations
import concurrent.futures
import os
import re
import time
from typing import Any, Dict, List

import requests
from etl.db import get_conn
from ai.agent.stocks import resolve_stock_codes


# ---------------------------------------------------------------------------
# 各工具实现
# ---------------------------------------------------------------------------
def _fn_get_quote(stock_code: str, indicator: str = None, days: int = 60) -> Dict[str, Any]:
    codes = resolve_stock_codes(stock_code)
    code = codes[0] if codes else None
    if not code:
        return {"error": "未识别到股票代码", "degraded": True}
    conn = get_conn()
    row = conn.execute(
        "SELECT trade_date, open, close, high, low, volume, amount "
        "FROM daily_kline WHERE stock_code=? AND k_type=1 ORDER BY trade_date DESC LIMIT 1",
        [code],
    ).fetchone()
    if not row:
        return {"error": f"无 {code} 行情数据", "degraded": True}
    closes = [r[0] for r in conn.execute(
        "SELECT close FROM daily_kline WHERE stock_code=? AND k_type=1 "
        "ORDER BY trade_date DESC LIMIT ?", [code, int(days)]
    ).fetchall()]
    closes = list(reversed(closes))
    ma20 = round(sum(closes[-20:]) / len(closes[-20:]), 2) if len(closes) >= 20 else None
    ma60 = round(sum(closes[-60:]) / len(closes[-60:]), 2) if len(closes) >= 60 else None
    return {
        "stock_code": code,
        "trade_date": str(row[0]),
        "open": row[1], "close": row[2], "high": row[3], "low": row[4],
        "volume": row[5], "amount": row[6],
        "ma20": ma20, "ma60": ma60,
        "indicator_requested": indicator,
    }


def _fn_get_financials(stock_code: str, report_type: str = None) -> Dict[str, Any]:
    codes = resolve_stock_codes(stock_code)
    code = codes[0] if codes else None
    if not code:
        return {"error": "未识别到股票代码", "degraded": True}
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT * FROM finance_core WHERE stock_code=? ORDER BY report_date DESC LIMIT 1",
            [code],
        )
        row = cur.fetchone()
        cols = [d[0] for d in cur.description]
    except Exception:
        return {"error": "财务表 finance_core 不可用", "degraded": True}
    if not row:
        return {"error": f"无 {code} 财务数据", "degraded": True}
    data = {cols[i]: row[i] for i in range(len(cols))}
    data.pop("stock_code", None)
    return {"stock_code": code, "report_date": data.get("report_date"), "financials": data}


def _fn_compare_stocks(codes: List[str], metrics: List[str] = None) -> Dict[str, Any]:
    resolved: List[str] = []
    for c in (codes or []):
        rc = resolve_stock_codes(c if isinstance(c, str) else str(c))
        resolved.append(rc[0] if rc else (c if isinstance(c, str) else None))
    resolved = [c for c in resolved if c]
    if len(resolved) < 2:
        return {"error": "对比至少需要 2 只股票", "degraded": True}
    from ai.analyzers import comparative
    res = comparative.run(stock_codes=resolved, save=False)
    if "error" in res:
        return {"error": res["error"], "degraded": True}
    return {"stock_codes": resolved, "comparison": res.get("response", "")}


def _fn_rag_retrieve(query: str, top_k: int = 5, stock_code: str = None) -> Dict[str, Any]:
    from knowledge import rag_chain
    res = rag_chain.query(question=query, stock_code=stock_code, top_k=int(top_k))
    if "error" in res:
        return {"error": res["error"], "degraded": True}
    return {"answer": res.get("answer", ""), "citations": res.get("citations", [])}


def _fn_summarize_filing(query: str, top_k: int = 5, stock_code: str = None) -> Dict[str, Any]:
    # 基于 RAG 检索到的财报/公告切片做要点摘要
    return _fn_rag_retrieve(query, top_k=top_k, stock_code=stock_code)


_CN_TO_AND = [("并且", " AND "), ("同时", " AND "), ("且", " AND "), ("&", " AND "),
              ("和", " AND "), ("或者", " OR "), ("或", " OR ")]
_ABOVE_KW = ("站上", "上穿", "上破", "升破", "突破", "高于", "大于", "金叉")
_BELOW_KW = ("跌破", "下穿", "下破", "失守", "低于", "小于", "死叉")
_NOISE_KW = ("买入", "卖出", "做多", "做空", "开仓", "平仓", "持有", "信号",
             "策略", "的时候", "时", "则", "就", "后", "买", "卖")


def _normalize_signal(sig: str) -> str:
    """把口语/驼峰式信号规范化成引擎 DSL，例如：

    '站上60日线买入' -> 'close > ma60'
    'ma60_breakup' / 'close_above_ma60' -> 'close > ma60'
    '站上60日线且RSI低于40' -> 'close > ma60 AND rsi14 < 40'
    """
    s = (sig or "").strip()
    if not s:
        return s
    for a, b in (("（", "("), ("）", ")"), ("＞", ">"), ("＜", "<"),
                 ("≥", ">="), ("≤", "<="), ("＝", "=")):
        s = s.replace(a, b)

    # 1) 驼峰/下划线式写法
    s = re.sub(r"(?i)close[_\s]*(?:above|over|breaks?[_\s]*above)[_\s]*ma(\d+)", r"close > ma\1", s)
    s = re.sub(r"(?i)close[_\s]*(?:below|under)[_\s]*ma(\d+)", r"close < ma\1", s)
    s = re.sub(r"(?i)ma(\d+)[_\s]*(?:golden[_\s]*cross|break(?:up|out)?|above|up)",
               r"close > ma\1", s)
    s = re.sub(r"(?i)ma(\d+)[_\s]*(?:dead[_\s]*cross|break[_\s]*down|below|down)",
               r"close < ma\1", s)
    s = re.sub(r"(?i)ma(\d+)[_\s]*cross(?:es|ing)?", r"close > ma\1", s)

    # 2) 中文指标与连接词
    s = re.sub(r"(\d+)\s*日(?:均)?线", r"ma\1", s)
    s = re.sub(r"(?i)(?<![a-z])rsi(?![0-9a-z_])", "rsi14", s)
    s = re.sub(r"(?i)(?<![a-z])macd(?![0-9a-z_])", "macd_hist", s)
    s = re.sub(r"(?i)(?<![a-z])kdj(?![0-9a-z_])", "kdj_k", s)
    s = s.replace("大于等于", " >= ").replace("小于等于", " <= ")
    for kw in _ABOVE_KW:
        s = s.replace(kw, " > ")
    for kw in _BELOW_KW:
        s = s.replace(kw, " < ")
    for a, b in _CN_TO_AND:
        s = s.replace(a, b)
    for kw in _NOISE_KW:
        s = s.replace(kw, " ")
    # 指标名统一小写（MA5 -> ma5，DSL 区分大小写）
    s = re.sub(r"(?i)\b(ma|rsi)\s*(\d+)", lambda m: m.group(1).lower() + m.group(2), s)
    s = re.sub(r"\s+", " ", s).strip()

    # 3) 缺操作数的悬空运算符：补 close（如「站上60日线」-> '> ma60' -> 'close > ma60'）
    s = re.sub(r"^([<>]=?)\s*", r"close \1 ", s).strip()
    s = re.sub(r"\s*([<>]=?)$", r" \1 close", s).strip()
    s = re.sub(r"\s*\b(AND|OR)\s*\b(AND|OR)\b\s*", r" \1 ", s, flags=re.IGNORECASE)

    # 4) 只有指标名、没有比较符时按常见语境补全
    if not re.search(r">=?|<=?|==|!=", s):
        t = s.lower().strip()
        m = re.fullmatch(r"ma(\d+)", t)
        if m:
            return f"close > ma{m.group(1)}"
        m = re.fullmatch(r"rsi(\d+)", t)
        if m:
            return f"rsi{m.group(1)} < 30"
        if t == "macd_hist":
            return "macd_hist > 0"
        if t == "kdj_k":
            return "kdj_k < 20"
    return s


def _run_backtest_nl_fallback(desc: str, code: str, start: str, end: str,
                              initial_cash: float) -> Dict[str, Any]:
    """DSL 解析失败时，用自然语言策略生成（LLM 翻译成策略代码后沙箱执行）兜底。"""
    try:
        from backtest.nl import run_nl_backtest
        r = run_nl_backtest(desc, stock_code=code, start=start, end=end,
                            initial_cash=float(initial_cash), save=False)
    except Exception:
        return {}
    if not r or not r.get("executed"):
        return {}
    m = r.get("metrics", {}) or {}
    summary = {
        "stock_code": code,
        "signal": desc,
        "parsed_by": "nl_codegen",
        "generated_code": r.get("generated_code"),
        "total_return_pct": round(m.get("total_return", 0) * 100, 2),
        "annual_return_pct": round(m.get("annual_return", 0) * 100, 2),
        "max_drawdown_pct": round(m.get("max_drawdown", 0) * 100, 2),
        "sharpe": m.get("sharpe"),
        "win_rate_pct": round(m.get("win_rate", 0) * 100, 2),
        "trade_count": len(r.get("trades", [])),
        "period": f"{start} ~ {end}",
        "degraded": False,
    }
    curve = r.get("equity_curve", []) or []
    if len(curve) > 160:
        step_c = max(1, len(curve) // 160)
        curve = curve[::step_c]
    trades = r.get("trades", []) or []
    if len(trades) > 100:
        trades = trades[:100]
    card = {
        "type": "backtest",
        "stock_code": code,
        "signal": desc,
        "period": f"{start} ~ {end}",
        "metrics": {
            "total_return_pct": summary["total_return_pct"],
            "annual_return_pct": summary["annual_return_pct"],
            "max_drawdown_pct": summary["max_drawdown_pct"],
            "sharpe": summary["sharpe"],
            "win_rate_pct": summary["win_rate_pct"],
            "trade_count": summary["trade_count"],
        },
        "equity_curve": curve,
        "trades": trades,
        "warning": None,
    }
    return summary, card


def _fn_run_backtest(stock_code: str, signal: str, start: str = None,
                     end: str = None, initial_cash: float = 1000000.0) -> Dict[str, Any]:
    codes = resolve_stock_codes(stock_code)
    code = codes[0] if codes else None
    if not code:
        return {"error": "未识别到股票代码", "degraded": True}
    if not signal:
        return {"error": "缺少 signal（买卖信号表达式）", "degraded": True}
    from datetime import date, timedelta
    from backtest.engine import run_backtest, BacktestRequest
    if not end:
        end = date.today().isoformat()
    if not start:
        start = (date.today() - timedelta(days=730)).isoformat()
    normalized = _normalize_signal(signal) or signal
    err = None
    res = None
    try:
        req = BacktestRequest(
            stock_code=code, start=start, end=end,
            signal=normalized, initial_cash=float(initial_cash),
        )
        res = run_backtest(req)
    except Exception as e:
        err = str(e)

    if res is None:
        # DSL 解析失败 -> 用自然语言策略生成兜底（LLM 翻译 + 沙箱执行）
        fb = _run_backtest_nl_fallback(signal, code, start, end, initial_cash)
        if fb:
            fb_summary, fb_card = fb
            return {"summary": fb_summary, "card": fb_card, "data_flag": "nl",
                    "normalized_signal": normalized, "parsed_by": "nl_codegen"}

        return {
            "error": f"回测失败：{err}",
            "normalized_signal": normalized,
            "supported_syntax": ["close > ma60", "ma5 > ma20",
                                 "close > ma60 AND rsi14 < 40", "macd_hist > 0"],
            "hint": ("signal 必须是 DSL 比较表达式（列 运算符 列/数值），不支持括号与策略名。"
                     "请改用 supported_syntax 里的写法重试**一次**；若仍失败，"
                     "请直接告知用户该条件暂无法回测，不要再反复重试。"),
            "degraded": True,
        }

    m = res.get("metrics", {})
    summary = {
        "stock_code": code,
        "signal": normalized,
        "total_return_pct": round(m.get("total_return", 0) * 100, 2),
        "annual_return_pct": round(m.get("annual_return", 0) * 100, 2),
        "max_drawdown_pct": round(m.get("max_drawdown", 0) * 100, 2),
        "sharpe": m.get("sharpe"),
        "win_rate_pct": round(m.get("win_rate", 0) * 100, 2),
        "trade_count": len(res.get("trades", [])),
        "period": f"{start} ~ {end}",
    }
    if normalized != signal:
        summary["input_signal"] = signal

    # 0 笔交易时给出可执行解释（资金不足买不起 1 手 / 区间内无买点）
    warning = summary.get("warning")
    if not res.get("trades"):
        lot_cost = None
        try:
            from backtest.data import load_kline
            d = load_kline(code, start, end)
            if d is not None and not d.empty:
                lot_cost = float(d["close"].iloc[-1]) * 100
        except Exception:
            lot_cost = None
        if lot_cost and lot_cost > float(initial_cash):
            warning = (
                f"区间内无交易：初始资金 {int(initial_cash)} 元不足以买入 1 手（100 股）{code}"
                f"（约需 {lot_cost:.0f} 元）。可提高 initial_cash 后重试。")
        else:
            warning = "区间内该信号未触发买入（无买点），因此绩效均为 0。"
        summary["warning"] = warning

    # 给前端渲染用的结构化卡片（权益曲线缩采样到 ~160 点，交易最多保留 100 笔）
    curve = res.get("equity_curve", []) or []
    if len(curve) > 160:
        step_c = max(1, len(curve) // 160)
        curve = curve[::step_c]
    trades = res.get("trades", []) or []
    if len(trades) > 100:
        trades = trades[:100]
    card = {
        "type": "backtest",
        "stock_code": code,
        "signal": normalized,
        "input_signal": signal if normalized != signal else None,
        "period": f"{start} ~ {end}",
        "metrics": {
            "total_return_pct": summary["total_return_pct"],
            "annual_return_pct": summary["annual_return_pct"],
            "max_drawdown_pct": summary["max_drawdown_pct"],
            "sharpe": summary["sharpe"],
            "win_rate_pct": summary["win_rate_pct"],
            "trade_count": summary["trade_count"],
        },
        "equity_curve": curve,
        "trades": trades,
        "warning": warning,
    }
    return {"summary": summary, "card": card, "data_flag": res.get("data_flag")}


def _fn_suggest_investment(stock_code: str, context: str = None) -> Dict[str, Any]:
    codes = resolve_stock_codes(stock_code)
    code = codes[0] if codes else None
    if not code:
        return {"error": "未识别到股票代码", "degraded": True}
    from ai.analyzers import run as analyze
    parts = []
    for t in ("valuation", "risk", "financial"):
        r = analyze(code, t, save=False)
        if "error" in r:
            parts.append(f"【{t}】{r['error']}")
        else:
            parts.append(r.get("response", ""))
    return {
        "stock_code": code,
        "viewpoint": "\n\n".join(parts),
        "note": "由估值/风险/财报分析器综合生成，仅供参考",
        "context": context,
    }


def _fn_web_search(query: str, top_k: int = 5) -> Dict[str, Any]:
    """联网搜索：MiniMax 服务端 web_search（优先） > akshare 个股新闻（兜底）。

    - MiniMax 服务端 web_search：无需额外 key，仅用 MINIMAX_API_KEY 即可，
      由 MiniMax 在服务端完成搜索并返回综合答案（Beta）。
    - akshare：仅 A 股个股新闻兜底（当非 MiniMax 模型或 MiniMax 搜索失败时使用）。
    """
    top_k = int(top_k) or 5

    # MiniMax 服务端 web_search：仅用 MINIMAX_API_KEY，无需 Tavily
    if (os.environ.get("LLM_PROVIDER", "mock").lower() == "minimax"
            and os.environ.get("MINIMAX_API_KEY")):
        try:
            mresp = requests.post(
                "https://api.minimax.cn/v1/responses",
                headers={"Authorization": f"Bearer {os.environ['MINIMAX_API_KEY']}"},
                json={
                    "model": os.environ.get("MINIMAX_MODEL", "MiniMax-M3"),
                    "input": query,
                    "tools": [{"type": "web_search"}],
                    "max_output_tokens": 1500,
                },
                timeout=85,
            )
            mresp.raise_for_status()
            mj = mresp.json()
            text = mj.get("output_text") or ""
            # 遍历 output，收集正文与 url_citation 注解（真实来源：标题+链接+摘要）
            citations = []
            for item in mj.get("output", []) or []:
                if item.get("type") != "message":
                    continue
                for c in item.get("content", []) or []:
                    if c.get("type") != "output_text":
                        continue
                    if not text:
                        text += c.get("text", "")
                    for a in c.get("annotations", []) or []:
                        if a.get("type") == "url_citation" and a.get("url"):
                            citations.append({
                                "title": (a.get("title") or "").strip(),
                                "url": a.get("url"),
                                "content": (a.get("content") or "")[:800],
                            })
            if text or citations:
                results = citations
                if not results:
                    # 兜底：从正文里的 markdown 链接抽取
                    links = re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", text)
                    results = [{"title": t.strip(), "url": u, "content": ""}
                               for t, u in links if len(t.strip()) > 1]
                return {"query": query, "source": "minimax_web",
                        "summary": text[:2000], "results": results}
        except Exception:
            pass  # 继续降级到 akshare

    # 兜底：akshare 新闻（若 query 能解析出股票代码则取个股新闻）
    try:
        from ai.agent.stocks import resolve_stock_codes
        import akshare as ak
        codes = resolve_stock_codes(query)
        if codes:
            df = ak.stock_news_em(symbol=codes[0]).head(top_k)
            rows = df.to_dict("records")
            return {"query": query, "source": "akshare_news",
                    "results": [{"title": r.get("新闻标题", ""),
                                 "content": str(r.get("新闻内容", "") or "")[:600],
                                 "date": str(r.get("发布时间", ""))} for r in rows]}
    except Exception:
        pass
    return {"query": query, "results": [], "degraded": True,
            "note": "当前非 MiniMax 模型或 MiniMax 联网搜索不可用，"
                    "且本地 A 股新闻检索不可用（确保 LLM_PROVIDER=minimax 并配置 MINIMAX_API_KEY）"}


def _fn_get_research_reports(stock_code: str, top_k: int = 5) -> Dict[str, Any]:
    """外部研报：调用 akshare 实时拉取个股研报（机构/评级/盈利预测）。"""
    codes = resolve_stock_codes(stock_code)
    code = codes[0] if codes else None
    if not code:
        return {"error": "未识别到股票代码", "degraded": True}
    try:
        import akshare as ak
        df = ak.stock_research_report_em(symbol=code)
        if df is None or getattr(df, "empty", True):
            return {"error": f"无 {code} 研报数据", "degraded": True}
        cols = list(df.columns)
        out = [{c: str(r[c]) for c in cols} for _, r in df.head(int(top_k)).iterrows()]
        return {"stock_code": code, "count": len(out), "reports": out}
    except Exception as e:
        return {"error": f"研报检索失败: {e}", "degraded": True}


def _fn_get_announcements(stock_code: str, top_k: int = 5) -> Dict[str, Any]:
    """外部公告：读取本地已接入的公告库（data_ingest.announcement 爬取自东方财富）。"""
    codes = resolve_stock_codes(stock_code)
    code = codes[0] if codes else None
    if not code:
        return {"error": "未识别到股票代码", "degraded": True}
    conn = get_conn()
    rows = conn.execute(
        "SELECT title, publish_date, source, url FROM doc "
        "WHERE doc_type='announcement' AND stock_code=? "
        "ORDER BY publish_date DESC LIMIT ?", [code, int(top_k)]
    ).fetchall()
    if not rows:
        return {"stock_code": code, "announcements": [],
                "note": "本地公告库暂无该股数据（可先运行 data_ingest.announcement 接入）",
                "degraded": True}
    return {"stock_code": code, "count": len(rows),
            "announcements": [{"title": r[0], "date": str(r[1]),
                               "source": r[2], "url": r[3]} for r in rows]}


def _fn_get_macro(top_k: int = 6) -> Dict[str, Any]:
    """宏观指标：取 macro_indicator 中各指标最新一期（CPI/PMI/LPR 等）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT indicator, trade_date, value, unit FROM macro_indicator "
        "ORDER BY indicator, trade_date DESC"
    ).fetchall()
    if not rows:
        return {"error": "宏观表 macro_indicator 暂无数据", "degraded": True}
    latest: Dict[str, Any] = {}
    for ind, dt, val, unit in rows:
        if ind not in latest:
            latest[ind] = {"date": str(dt), "value": val, "unit": unit}
    return {"macro": latest}


def _fn_get_industry(stock_code: str) -> Dict[str, Any]:
    """行业归属与可比同业：读 industry_chain，并取同板块部分成分作为可比样本。"""
    codes = resolve_stock_codes(stock_code)
    code = codes[0] if codes else None
    if not code:
        return {"error": "未识别到股票代码", "degraded": True}
    conn = get_conn()
    rows = conn.execute(
        "SELECT sector_name, sector_type, sector_code FROM industry_chain WHERE stock_code=?",
        [code],
    ).fetchall()
    if not rows:
        return {"stock_code": code, "industries": [],
                "note": "未找到行业归属（先运行 data_ingest.industry_chain）", "degraded": True}
    peers: Dict[str, list] = {}
    for name, stype, scode in rows:
        try:
            pr = conn.execute(
                "SELECT stock_code FROM industry_chain WHERE sector_type=? AND sector_code=? "
                "AND stock_code!=? LIMIT 10", [stype, scode, code]
            ).fetchall()
            peers[name] = [p[0] for p in pr]
        except Exception:
            peers[name] = []
    return {"stock_code": code,
            "industries": [{"name": r[0]} for r in rows],
            "peers_sample": peers}


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
TOOLS: Dict[str, Dict[str, Any]] = {
    "get_quote": {
        "description": "查询股票最新行情与技术指标（最新价/涨跌/MA20/MA60 等）",
        "params": {
            "stock_code": {"required": True, "type": "str", "desc": "股票代码或名称"},
            "indicator": {"required": False, "type": "str", "desc": "指定指标（可选）"},
            "days": {"required": False, "type": "int", "desc": "计算均线的回看天数，默认 60"},
        },
        "fn": _fn_get_quote,
    },
    "get_financials": {
        "description": "查询股票财务数据（利润/资产/现金流/估值指标摘要）",
        "params": {
            "stock_code": {"required": True, "type": "str", "desc": "股票代码或名称"},
            "report_type": {"required": False, "type": "str", "desc": "报告类型（可选）"},
        },
        "fn": _fn_get_financials,
    },
    "compare_stocks": {
        "description": "横向对比多只股票的财务与估值指标（PE/PB/ROE/毛利率等）",
        "params": {
            "codes": {"required": True, "type": "list[str]", "desc": "至少 2 只股票代码或名称"},
            "metrics": {"required": False, "type": "list[str]", "desc": "关注的指标（可选）"},
        },
        "fn": _fn_compare_stocks,
    },
    "rag_retrieve": {
        "description": "在知识库（财报/公告/研报/笔记）中检索相关切片并回答",
        "params": {
            "query": {"required": True, "type": "str", "desc": "检索问题"},
            "top_k": {"required": False, "type": "int", "desc": "召回条数，默认 5"},
            "stock_code": {"required": False, "type": "str", "desc": "关联股票代码（可选）"},
        },
        "fn": _fn_rag_retrieve,
    },
    "summarize_filing": {
        "description": "基于 RAG 检索到的财报/公告切片，生成要点摘要",
        "params": {
            "query": {"required": True, "type": "str", "desc": "摘要问题"},
            "top_k": {"required": False, "type": "int", "desc": "召回条数，默认 5"},
            "stock_code": {"required": False, "type": "str", "desc": "关联股票代码（可选）"},
        },
        "fn": _fn_summarize_filing,
    },
    "run_backtest": {
        "description": "对股票运行规则回测，返回收益/回撤/夏普/胜率等绩效。"
                       "signal 必须是 DSL 比较表达式（列 运算符 列/数值），"
                       "如 'close > ma60'、'ma5 > ma20'、'close > ma60 AND rsi14 < 40'；"
                       "**不要**传 'ma60_breakup'、'close_above_ma60' 这类策略名（也可传自然语言描述，"
                       "系统会自动翻译）。不支持括号。",
        "params": {
            "stock_code": {"required": True, "type": "str", "desc": "股票代码或名称"},
            "signal": {"required": True, "type": "str",
                       "desc": "买卖信号 DSL，如 'close > ma60'（站上60日线）、"
                               "'ma5 > ma20'（5日线上穿20日线）、"
                               "'close > ma60 AND rsi14 < 40'"},
            "start": {"required": False, "type": "str", "desc": "开始日期 YYYY-MM-DD"},
            "end": {"required": False, "type": "str", "desc": "结束日期 YYYY-MM-DD"},
            "initial_cash": {"required": False, "type": "float",
                             "desc": "初始资金，默认 1000000（高价股需≥1手市值，约股价×100）"},
        },
        "fn": _fn_run_backtest,
        "timeout": 60,   # 自然语言兜底会多一次 LLM 调用
    },
    "suggest_investment": {
        "description": "综合估值/风险/财报分析，生成该股票的研究观点（仅供参考）",
        "params": {
            "stock_code": {"required": True, "type": "str", "desc": "股票代码或名称"},
            "context": {"required": False, "type": "str", "desc": "补充上下文（可选）"},
        },
        "fn": _fn_suggest_investment,
    },
    "web_search": {
        "description": "联网搜索外部资料（使用 MiniMax 服务端 web_search；非 MiniMax 模型或失败时降级 akshare 个股新闻）",
        "params": {
            "query": {"required": True, "type": "str", "desc": "搜索问题/关键词"},
            "top_k": {"required": False, "type": "int", "desc": "返回条数，默认 5"},
        },
        "fn": _fn_web_search,
        "timeout": 90,   # 服务端联网搜索较慢，放宽超时
        "retries": 0,
    },
    "get_research_reports": {
        "description": "拉取个股最新外部研报（机构/评级/盈利预测，akshare 实时）",
        "params": {
            "stock_code": {"required": True, "type": "str", "desc": "股票代码或名称"},
            "top_k": {"required": False, "type": "int", "desc": "返回条数，默认 5"},
        },
        "fn": _fn_get_research_reports,
        "timeout": 40,   # akshare 实时拉取可能较慢
    },
    "get_announcements": {
        "description": "读取个股已接入的外部公告（来源：东方财富，先于 data_ingest.announcement 入库）",
        "params": {
            "stock_code": {"required": True, "type": "str", "desc": "股票代码或名称"},
            "top_k": {"required": False, "type": "int", "desc": "返回条数，默认 5"},
        },
        "fn": _fn_get_announcements,
    },
    "get_macro": {
        "description": "取最新宏观指标（CPI/PMI/LPR 等，来自 macro_indicator）",
        "params": {
            "top_k": {"required": False, "type": "int", "desc": "保留（兼容）"},
        },
        "fn": _fn_get_macro,
    },
    "get_industry": {
        "description": "查股票所属行业与可比同业样本（来自 industry_chain）",
        "params": {
            "stock_code": {"required": True, "type": "str", "desc": "股票代码或名称"},
        },
        "fn": _fn_get_industry,
    },
}


def call_tool(name: str, args: Dict[str, Any], timeout: int = 15, retries: int = 1) -> Dict[str, Any]:
    """执行一个白名单工具。返回 observation dict。"""
    spec = TOOLS.get(name)
    if not spec:
        return {
            "error": f"未知工具 `{name}`，不在白名单内。可用工具：{', '.join(TOOLS.keys())}",
            "rejected": True,
        }
    # 参数校验
    validated: Dict[str, Any] = {}
    for pname, pinfo in spec["params"].items():
        if pname in args and args[pname] is not None:
            validated[pname] = args[pname]
        elif pinfo.get("required"):
            return {"error": f"缺少必填参数 `{pname}`", "param_error": True}

    def _run():
        return spec["fn"](**validated)

    eff_timeout = spec.get("timeout", timeout)
    eff_retries = spec.get("retries", retries)
    attempt = 0
    last = None
    while attempt <= eff_retries:
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_run)
                return fut.result(timeout=eff_timeout)
        except concurrent.futures.TimeoutError:
            last = {"error": "数据暂不可用（工具超时）", "degraded": True}
        except Exception as e:  # noqa
            return {"error": f"工具执行异常: {e}", "degraded": True}
        attempt += 1
    return last or {"error": "工具调用失败", "degraded": True}


def tool_specs_text() -> str:
    """生成给 LLM 看的工具说明文本。"""
    lines = []
    for name, spec in TOOLS.items():
        ps = ", ".join(
            f"{p}({'必填' if i.get('required') else '可选'})" for p, i in spec["params"].items()
        )
        lines.append(f"- {name}: {spec['description']} | 参数[{ps}]")
    return "\n".join(lines)
