"""量化选股（NL 自然语言 → 全市场批量筛选）。

管线复用 backtest.nl 的代码生成与 backtest.sandbox 的沙箱执行，
区别在于：对全市场股票（剔除 ST/次新）批量运行同一段策略代码，
取每只股票「最新交易日」信号为 True 的标的作为命中结果。
"""
from __future__ import annotations

import logging
import os
import re
import threading
import concurrent.futures as cf
from datetime import timedelta
from typing import Callable, Dict, List, Optional

import pandas as pd

from etl.db import get_conn
from backtest.nl import generate_code
from backtest.security import validate_code_ast
from backtest.sandbox import execute_signal, execute_signal_pooled, SandboxError

logger = logging.getLogger(__name__)

# 选股需要的回看交易日窗口（取最近约 1.5 年，足以支撑 MA250 等长周期指标）
_LOOKBACK_DAYS = 540
# 单股最少交易日（不足则跳过，避免 MA 等长周期指标失真）
_MIN_ROWS = 120
# 上市不足该天数视为次新股，剔除
_MIN_LIST_DAYS = 365

_ST_RE = re.compile(r"(^|\s|\*)\s*ST", re.IGNORECASE)


def _latest_trade_date():
    try:
        conn = get_conn(read_only=True)
        try:
            row = conn.execute(
                "SELECT MAX(trade_date) FROM daily_kline WHERE k_type=1"
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def build_universe(exclude_st: bool = True, min_list_days: int = _MIN_LIST_DAYS) -> List[Dict]:
    """构建扫描股票池。

    返回 [{stock_code, short_name, exchange, list_date}, ...]。
    - exclude_st：剔除名称含 ST/*ST 的风险警示股；
    - min_list_days：剔除上市不足该天数的次新股（按最新交易日回推）。
    """
    latest = _latest_trade_date()
    cutoff = None
    if latest:
        try:
            latest_dt = pd.Timestamp(str(latest))
            cutoff = (latest_dt - timedelta(days=min_list_days)).strftime("%Y-%m-%d")
        except Exception:
            cutoff = None

    conn = get_conn(read_only=True)
    try:
        rows = conn.execute(
            "SELECT stock_code, short_name, exchange, list_date FROM stock_basic"
        ).fetchall()
    finally:
        conn.close()

    out = []
    for code, name, exch, list_date in rows:
        name = name or ""
        if exclude_st and _ST_RE.search(name):
            continue
        if cutoff and list_date and str(list_date) > cutoff:
            continue
        out.append({
            "stock_code": code,
            "short_name": name,
            "exchange": exch or "",
            "list_date": str(list_date) if list_date else "",
        })
    return out


def load_industry_map() -> Dict[str, str]:
    """一次拉取全市场行业归属 {stock_code: industry_name}。

    口径优先级：申万一级 > 申万二级 > 国民经济二级 > 国民经济一级。
    adata 对部分股票（多为深市次新）仅提供国民经济行业分类，无申万细分，
    此时降级显示更具体的国民经济二级（如「造纸和纸制品业」）。
    库表缺失/异常时返回空字典，不影响选股主流程。
    """
    conn = None
    try:
        conn = get_conn(read_only=True)
        rows = conn.execute(
            "SELECT stock_code, industry_name, industry_type FROM stock_industry"
        ).fetchall()
    except Exception as e:
        logger.warning("行业映射加载失败，板块列留空：%s", e)
        return {}
    finally:
        if conn is not None:
            conn.close()

    sw1, sw2, gb2, gb1 = {}, {}, {}, {}
    for code, name, itype in rows:
        if not code or not name:
            continue
        # adata 对同一接口混回两种口径：申万（申万一级/申万二级）与
        # 国民经济行业分类（一级/二级，如 C00000 制造业）。两者互斥。
        if itype == "申万一级":
            sw1.setdefault(code, name)
        elif itype == "申万二级":
            sw2.setdefault(code, name)
        elif itype == "一级":
            gb1.setdefault(code, name)
        elif itype == "二级":
            gb2.setdefault(code, name)
    out: Dict[str, str] = {}
    for code in set(sw1) | set(sw2) | set(gb1) | set(gb2):
        # 板块列口径优先级：申万一级 > 申万二级 > 国民经济二级 > 国民经济一级
        # （无申万细分的股票降级显示更具体的国民经济二级，而非笼统的「制造业」）
        out[code] = (sw1.get(code) or sw2.get(code)
                     or gb2.get(code) or gb1.get(code))
    return out


def load_kline_bulk(since_date: str) -> Dict[str, pd.DataFrame]:
    """一次 SQL 拉取全市场自 since_date 起的日线，按股票分组返回。

    优先使用复权(adjust_type=1)，缺失则退回不复权(adjust_type=0)。
    """
    conn = get_conn(read_only=True)
    try:
        df = conn.execute(
            """
            SELECT stock_code, trade_date, open, high, low, close, volume, amount, adjust_type
            FROM daily_kline
            WHERE k_type = 1 AND trade_date >= ?
            ORDER BY stock_code, trade_date
            """,
            [since_date],
        ).df()
    finally:
        conn.close()

    if df is None or df.empty:
        return {}

    out: Dict[str, pd.DataFrame] = {}
    for code, g in df.groupby("stock_code", sort=False):
        # 该股票优先取复权数据
        pref = int(g["adjust_type"].max())
        g = g[g["adjust_type"] == pref].copy()
        g = g.sort_values("trade_date").reset_index(drop=True)
        g = g[["trade_date", "open", "high", "low", "close", "volume", "amount"]]
        out[code] = g
    return out


def _sc_citations(since: str) -> List[Dict[str, str]]:
    """选股结果来源标注：全市场日行情（回溯窗口）。"""
    return [{
        "type": "data",
        "ref": "daily_kline",
        "label": f"全市场日行情（daily_kline，回溯窗口自 {since}）",
        "date": "",
    }]


def _sc_confidence(total: int, scanned: int, n_matches: int) -> float:
    """选股置信度（0-1，确定性推导）：由扫描覆盖率决定。

    基准 0.5 + 覆盖率(扫描/总数)*0.4；有命中再 +0.1。无样本取 0.3。
    """
    if not total:
        return 0.3
    cov = scanned / total
    base = 0.5 + 0.4 * cov
    if n_matches:
        base += 0.1
    return round(min(0.99, base), 2)


def run_screener(
    nl: str,
    exclude_st: bool = True,
    min_list_days: int = _MIN_LIST_DAYS,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Dict:
    """端到端运行一次自然语言选股，始终返回 dict。

    progress(scanned, total)：每扫描一只股票回调一次，便于前端展示进度。
    命中判定：最新交易日 signal 为 True（即「当前形态符合」）。
    """
    if not nl or not nl.strip():
        return {"ok": False, "error": "请输入选股的自然语言描述。",
                "generated_code": None, "matches": [], "total": 0,
                "scanned": 0, "skipped": 0, "citations": [], "confidence": 0.3,
                "disclaimer": True}

    # 1) 生成代码
    code, llm_err = generate_code(nl, None)
    if not code:
        if llm_err:
            return {"ok": False, "error":
                    f"大模型生成失败（{llm_err}）。请稍后重试，或改用更明确的描述"
                    f"（如「5日均线上穿20日均线」「MACD金叉」）。",
                    "generated_code": None, "matches": [], "total": 0,
                    "scanned": 0, "skipped": 0, "llm_err": llm_err,
                    "citations": [], "confidence": 0.3, "disclaimer": True}
        return {"ok": False, "error":
                "无法从描述中识别出可用指标。请换用更明确的描述，例如"
                "「5日均线上穿20日均线」「MACD金叉、RSI小于30」。",
                "generated_code": None, "matches": [], "total": 0,
                "scanned": 0, "skipped": 0, "citations": [], "confidence": 0.3,
                "disclaimer": True}

    # 2) AST 白名单校验
    ok, reason, suggestions = validate_code_ast(code)
    if not ok:
        return {"ok": False, "error": reason,
                "generated_code": code, "suggestions": suggestions,
                "matches": [], "total": 0, "scanned": 0, "skipped": 0,
                "citations": [], "confidence": 0.3, "disclaimer": True}

    # 3) 构建股票池 + 批量取数
    universe = build_universe(exclude_st=exclude_st, min_list_days=min_list_days)
    total = len(universe)
    latest = _latest_trade_date()
    since = "1970-01-01"
    if latest:
        try:
            since = (pd.Timestamp(str(latest)) - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        except Exception:
            since = "1970-01-01"
    klines = load_kline_bulk(since)
    industry_map = load_industry_map()

    matches: List[Dict] = []
    scanned = 0
    skipped = 0
    skipped_short = 0
    scanned_lock = threading.Lock()

    def _scan_one(u):
        """单股执行：返回 ('hit', match_dict) / ('skip', None) / ('no', None)。"""
        code_id = u["stock_code"]
        df = klines.get(code_id)
        if df is None or len(df) < _MIN_ROWS:
            return ("skip_short", code_id)
        try:
            res = execute_signal_pooled(code, df, capture_debug=True)
            if len(res) == 3:
                signal_list, _logs, debug = res
            else:
                signal_list, _logs = res
                debug = {}
        except SandboxError:
            # 单股异常不影响整体（如某股数据导致除零），记 skipped
            return ("skip", code_id)
        except Exception:
            return ("skip", code_id)

        if not signal_list:
            return ("skip", code_id)
        # 最新交易日信号为 True 即命中
        if not bool(signal_list[-1]):
            return ("no", code_id)

        close = float(df["close"].iloc[-1])
        pct = None
        if len(df) >= 2:
            prev = float(df["close"].iloc[-2])
            if prev:
                pct = round((close / prev - 1.0) * 100.0, 2)
        dbg = {k: (round(float(v), 3) if isinstance(v, (int, float)) else v)
               for k, v in (debug or {}).items()}
        return ("hit", {
            "stock_code": code_id,
            "short_name": u["short_name"],
            "exchange": u["exchange"],
            "industry": industry_map.get(code_id, ""),
            "trade_date": str(df["trade_date"].iloc[-1]),
            "close": round(close, 3),
            "pct_change": pct,
            "signal_debug": dbg,
        })

    # 并发扫描：子进程执行释放 GIL，线程池可显著缩短全市场耗时
    workers = min(32, (os.cpu_count() or 4) * 3)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_scan_one, u): u for u in universe}
        for fut in cf.as_completed(futs):
            r = fut.result()
            with scanned_lock:
                scanned += 1
                cur = scanned
            if r[0] == "hit":
                matches.append(r[1])
            elif r[0] in ("skip", "skip_short"):
                skipped += 1
                if r[0] == "skip_short":
                    skipped_short += 1
            if progress:
                progress(cur, total)

    logger.info("选股完成：总数=%d 扫描=%d 命中=%d 跳过=%d(数据不足=%d)",
                total, scanned, len(matches), skipped, skipped_short)

    return {
        "ok": True,
        "generated_code": code,
        "matches": matches,
        "total": total,
        "scanned": scanned,
        "skipped": skipped,
        "skipped_short": skipped_short,
        "llm_err": llm_err,
        "citations": _sc_citations(since),
        "confidence": _sc_confidence(total, scanned, len(matches)),
        "disclaimer": True,
    }
