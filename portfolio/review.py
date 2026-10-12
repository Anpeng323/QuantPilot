"""
AI 交易复盘：
- 计算 win_rate、avg_return 等基础指标
- 用 LLM 生成自然语言总结 + 改进建议
"""
import sys
import uuid
import time
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jinja2 import Template
from etl.db import get_conn
from ai import client as llm_client
from ai.prompts import PROMPT_REVIEW
from ai.memory import load_memory


def compute_basic_stats() -> dict:
    """计算交易统计指标"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT trade_id, stock_code, direction, price, quantity, amount, trade_date, logic_text
        FROM trade
        ORDER BY trade_date DESC LIMIT 200
    """).fetchall()

    if not rows:
        return {"trade_count": 0, "win_rate": None, "avg_return": None,
                "total_return": None, "trades": []}

    trades = []
    # 用最近一次交易价 vs 当日最新价估算盈亏（v1 简化）
    for r in rows:
        tid, code, d, price, qty, amt, date_, logic = r
        cur = conn.execute("""
            SELECT close FROM daily_kline WHERE stock_code=? AND k_type=1
            ORDER BY trade_date DESC LIMIT 1
        """, [code]).fetchone()
        cur_price = cur[0] if cur else None
        ret = None
        if d == "buy" and cur_price:
            ret = (cur_price / price - 1) * 100
        elif d == "sell" and cur_price:
            ret = (1 - cur_price / price) * 100
        trades.append({
            "trade_id": tid, "stock_code": code, "direction": d,
            "price": price, "quantity": qty, "amount": amt,
            "trade_date": str(date_), "logic": logic or "",
            "current_price": cur_price, "return_pct": round(ret, 2) if ret is not None else None,
        })

    rets = [t["return_pct"] for t in trades if t["return_pct"] is not None]
    if not rets:
        return {"trade_count": len(trades), "win_rate": None, "avg_return": None,
                "total_return": None, "trades": trades}

    wins = sum(1 for r in rets if r > 0)
    return {
        "trade_count": len(trades),
        "win_rate": round(wins / len(rets) * 100, 2),
        "avg_return": round(sum(rets) / len(rets), 2),
        "best": max(rets), "worst": min(rets),
        "total_return": round(sum(rets), 2),
        "trades": trades,
    }


def run_review(period: str = "all", save: bool = True) -> dict:
    """跑一次 AI 复盘"""
    stats = compute_basic_stats()
    if stats["trade_count"] == 0:
        return {"error": "无交易可复盘。请先录入交易记录。"}

    # 简化 trades 描述（避免 prompt 过大）
    trades_brief = [{
        "date": t["trade_date"][:10],
        "stock_code": t["stock_code"],
        "direction": t["direction"],
        "return_pct": t["return_pct"],
        "logic": t["logic"][:120],
    } for t in stats["trades"][:30]]

    user_note = load_memory().get("notes", "")
    prompt = Template(PROMPT_REVIEW).render(
        trades=json.dumps(trades_brief, ensure_ascii=False, indent=2),
        user_note=user_note or "(无)",
    )

    t0 = time.time()
    resp = llm_client.chat([{"role": "user", "content": prompt}], temperature=0.4, max_tokens=1500)
    dur = int((time.time() - t0) * 1000)

    rid = uuid.uuid4().hex[:12]
    summary = resp["text"]
    if save:
        conn = get_conn()
        conn.execute("""
            INSERT INTO trade_review (review_id, period, ai_summary, win_rate, avg_return, total_return, trade_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, current_timestamp)
        """, [rid, period, summary,
              stats.get("win_rate"), stats.get("avg_return"),
              stats.get("total_return"), stats.get("trade_count")])

    return {
        "review_id": rid,
        "stats": {k: stats[k] for k in ("trade_count","win_rate","avg_return","best","worst","total_return")},
        "ai_summary": summary,
        "model": resp["model"],
        "tokens": resp["total_tokens"],
        "duration_ms": dur,
    }


def list_reviews(limit: int = 10) -> list:
    conn = get_conn()
    rows = conn.execute("""
        SELECT review_id, period, win_rate, avg_return, total_return, trade_count, created_at
        FROM trade_review ORDER BY created_at DESC LIMIT ?
    """, [limit]).fetchall()
    return [{
        "review_id": r[0], "period": r[1],
        "win_rate": r[2], "avg_return": r[3], "total_return": r[4],
        "trade_count": r[5], "created_at": str(r[6]),
    } for r in rows]
