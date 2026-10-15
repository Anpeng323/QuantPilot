"""
v1.1 新增路由注册。所有 Pydantic 模型定义在模块顶层，避免嵌套定义导致 forward ref 问题。
"""
from __future__ import annotations
from typing import List, Dict, Any, Optional
from pathlib import Path
import json
import os

from fastapi import APIRouter, HTTPException, Body, Query
from pydantic import BaseModel

from etl.db import get_conn


# ---------- Pydantic 模型（模块顶层） ----------
class PromptIn(BaseModel):
    template_id: Optional[str] = None
    name: str
    category: str = "custom"
    body: str
    variables: list = []
    is_builtin: bool = False


class PromptUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    body: Optional[str] = None
    variables: Optional[list] = None


class AlertRuleIn(BaseModel):
    name: str
    target_type: str = "stock"
    target_value: str
    metric: str
    operator: str
    threshold: float
    channel: str = "web"
    is_active: bool = True


# ---------- 注册函数 ----------
# ============================================================
# 规则回测引擎
# ============================================================
def _add_backtest_routes(router: APIRouter):
    from backtest.engine import BacktestRequest, run_backtest
    from backtest.cost import CostConfig
    from backtest.signal import validate_signal
    from backtest.errors import BacktestError, SignalParseError
    from backtest.benchmark import DEFAULT_BENCHMARK, list_benchmarks
    from backtest import repo

    def _benchmark_of(payload: dict):
        """基准指数：未指定时默认沪深300；显式传 none/off/无 则关闭对标。"""
        raw = (payload or {}).get("benchmark")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return DEFAULT_BENCHMARK
        code = str(raw).strip()
        return None if code.lower() in ("none", "off", "no", "-", "无", "不对比") else code

    @router.get("/backtest/benchmarks")
    def backtest_benchmarks():
        return {"benchmarks": list_benchmarks(), "default": DEFAULT_BENCHMARK}

    @router.post("/backtest/run")
    def backtest_run(payload: dict = Body(...)):
        stock_code = (payload.get("stock_code") or "").strip()
        signal = (payload.get("signal") or "").strip()
        start = (payload.get("start") or "").strip()
        end = (payload.get("end") or "").strip()
        days = payload.get("days")
        if not stock_code:
            raise HTTPException(400, "stock_code required")
        if not signal:
            raise HTTPException(400, "signal required")
        if not start or not end:
            if days:
                from backtest.nl import resolve_default_range
                end, start = resolve_default_range(days)
            else:
                raise HTTPException(400, "start/end 或 days 必须提供其一")
        if start > end:
            raise HTTPException(400, "回测区间无效，请检查起止日期（start 需早于 end）")
        try:
            initial_cash = float(payload.get("initial_cash", 100000))
        except (TypeError, ValueError):
            raise HTTPException(400, "initial_cash 必须为数字")
        if initial_cash <= 0:
            raise HTTPException(400, "initial_cash 需大于 0")
        fill_price = payload.get("fill_price", "close")
        if fill_price not in ("open", "close"):
            raise HTTPException(400, "fill_price 仅支持 open / close")

        ok, err = validate_signal(signal)
        if not ok:
            raise HTTPException(400, err)

        cost_d = payload.get("cost") or {}
        cost = CostConfig(
            commission=float(cost_d.get("commission", 0.00025)),
            min_commission=float(cost_d.get("min_commission", 5.0)),
            stamp=float(cost_d.get("stamp", 0.0005)),
            transfer=float(cost_d.get("transfer", 0.00001)),
            slippage=float(cost_d.get("slippage", 0.0)),
        )
        req = BacktestRequest(
            stock_code=stock_code, start=start, end=end, signal=signal,
            initial_cash=initial_cash, fill_price=fill_price, cost=cost,
            benchmark=_benchmark_of(payload), save=bool(payload.get("save", False)),
        )
        try:
            result = run_backtest(req)
        except SignalParseError as e:
            raise HTTPException(400, str(e))
        except BacktestError as e:
            raise HTTPException(400, str(e))

        task_id = repo.save_task(req, result) if req.save else None
        result["task_id"] = task_id
        return result

    @router.post("/backtest/signal/validate")
    def backtest_validate(payload: dict = Body(...)):
        ok, err = validate_signal((payload or {}).get("signal", ""))
        return {"valid": ok, "error": err}

    @router.get("/backtest/tasks")
    def backtest_tasks(limit: int = Query(20, ge=1, le=100)):
        return {"tasks": repo.list_tasks(limit)}

    @router.get("/backtest/tasks/{task_id}")
    def backtest_task_detail(task_id: str):
        task = repo.get_task(task_id)
        if not task:
            raise HTTPException(404, "回测任务不存在")
        return task

    # ============ Phase 3：自然语言策略自动回测 ============
    from backtest import nl as _nl
    from backtest.cost import CostConfig as _CostConfig
    from backtest.errors import BacktestError as _BacktestError
    from backtest.errors import SignalParseError as _SignalParseError

    @router.get("/backtest/nl/indicators")
    def nl_indicators():
        return {"indicators": _nl.describe_indicators()}

    @router.post("/backtest/nl/run")
    def nl_backtest_run(payload: dict = Body(...)):
        nl = (payload.get("natural_language") or "").strip()
        stock_code = (payload.get("stock_code") or "").strip()
        if not nl:
            raise HTTPException(400, "natural_language required")
        if not stock_code:
            raise HTTPException(400, "stock_code required")
        # 兼容直接传股票名称/简称（与搜索栏一致的模糊查询），解析为 6 位代码
        if not (len(stock_code) == 6 and stock_code.isdigit()):
            try:
                from ai.agent.stocks import resolve_stock_codes
                cands = resolve_stock_codes(stock_code)
            except Exception:
                cands = []
            if not cands:
                raise HTTPException(400, f"未找到股票「{stock_code}」，请输入 6 位代码或完整名称")
            stock_code = cands[0]

        start = (payload.get("start") or "").strip()
        end = (payload.get("end") or "").strip()
        days = payload.get("days")

        try:
            initial_cash = float(payload.get("initial_cash", 100000))
        except (TypeError, ValueError):
            raise HTTPException(400, "initial_cash 必须为数字")
        if initial_cash <= 0:
            raise HTTPException(400, "initial_cash 需大于 0")
        fill_price = payload.get("fill_price", "close")
        if fill_price not in ("open", "close"):
            raise HTTPException(400, "fill_price 仅支持 open / close")

        cost_d = payload.get("cost") or {}
        cost = _CostConfig(
            commission=float(cost_d.get("commission", 0.00025)),
            min_commission=float(cost_d.get("min_commission", 5.0)),
            stamp=float(cost_d.get("stamp", 0.0005)),
            transfer=float(cost_d.get("transfer", 0.00001)),
            slippage=float(cost_d.get("slippage", 0.0)),
        )

        try:
            result = _nl.run_nl_backtest(
                nl, stock_code=stock_code,
                start=start or None, end=end or None, days=days,
                initial_cash=initial_cash, fill_price=fill_price,
                cost=cost, benchmark=_benchmark_of(payload),
                save=bool(payload.get("save", False)), name=payload.get("name"),
            )
        except (_BacktestError, _SignalParseError) as e:
            raise HTTPException(400, str(e))
        return result

    @router.get("/backtest/strategies")
    def nl_strategies(limit: int = Query(50, ge=1, le=200)):
        from backtest import repo as _repo
        return {"strategies": _repo.list_strategies(limit)}

    @router.get("/backtest/strategies/{sid}")
    def nl_strategy_detail(sid: str):
        from backtest import repo as _repo
        s = _repo.get_strategy(sid)
        if not s:
            raise HTTPException(404, "策略记录不存在")
        return s


def register_v1_1_routes(app):
    router = APIRouter(prefix="/api/v1", tags=["v1.1"])
    _add_prompts_routes(router)
    _add_alerts_routes(router)
    _add_indicators_routes(router)
    _add_portfolio_routes(router)
    _add_exports_routes(router)
    _add_router_routes(router)
    _add_backtest_routes(router)
    app.include_router(router)


# ============================================================
# Prompt 模板
# ============================================================
def _add_prompts_routes(router: APIRouter):
    from prompts.template import PromptTemplate, seed_builtin_templates

    @router.get("/prompts")
    def list_prompts():
        conn = get_conn()
        seed_builtin_templates(conn)
        return [t.__dict__ for t in PromptTemplate.list_all(conn)]

    @router.post("/prompts")
    def create_prompt(p: PromptIn):
        conn = get_conn()
        t = PromptTemplate.create(
            conn=conn, template_id=p.template_id, name=p.name, category=p.category,
            body=p.body, variables=p.variables, is_builtin=p.is_builtin,
        )
        return t.__dict__

    @router.get("/prompts/{tid}")
    def get_prompt(tid: str):
        conn = get_conn()
        t = PromptTemplate.get(conn, tid)
        if not t:
            raise HTTPException(404, "模板不存在")
        return t.__dict__

    @router.put("/prompts/{tid}")
    def update_prompt(tid: str, p: PromptUpdate):
        conn = get_conn()
        kwargs = {k: v for k, v in p.dict().items() if v is not None}
        t = PromptTemplate.update(conn, tid, **kwargs)
        return t.__dict__

    @router.delete("/prompts/{tid}")
    def delete_prompt(tid: str):
        conn = get_conn()
        try:
            ok = PromptTemplate.delete(conn, tid)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        if not ok:
            raise HTTPException(404, "模板不存在")
        return {"status": "ok"}


# ============================================================
# 预警规则
# ============================================================
def _add_alerts_routes(router: APIRouter):
    from alerts.engine import AlertRule, AlertEngine

    @router.get("/alerts/rules")
    def list_rules():
        conn = get_conn()
        return [r.__dict__ for r in AlertRule.list_all(conn)]

    @router.post("/alerts/rules")
    def create_rule(r: AlertRuleIn):
        conn = get_conn()
        rule = AlertRule.create(
            conn=conn, name=r.name, target_type=r.target_type,
            target_value=r.target_value, metric=r.metric, operator=r.operator,
            threshold=r.threshold, channel=r.channel, is_active=r.is_active,
        )
        return rule.__dict__

    @router.delete("/alerts/rules/{rid}")
    def delete_rule(rid: str):
        conn = get_conn()
        ok = AlertRule.delete(conn, rid)
        if not ok:
            raise HTTPException(404, "规则不存在")
        return {"status": "ok"}

    @router.post("/alerts/run")
    def run_alerts():
        conn = get_conn()
        events = AlertEngine.run_all(conn)
        return [ev.to_dict() for ev in events]

    @router.get("/alerts/events")
    def list_events(limit: int = 100):
        conn = get_conn()
        rows = conn.execute(
            "SELECT event_id, rule_id, stock_code, metric, value, triggered_at, message, sent_status "
            "FROM alert_event ORDER BY triggered_at DESC LIMIT ?",
            [limit],
        ).fetchall()
        return [
            {"event_id": r[0], "rule_id": r[1], "stock_code": r[2],
             "metric": r[3], "value": r[4], "triggered_at": str(r[5]) if r[5] else None,
             "message": r[6], "sent_status": r[7]}
            for r in rows
        ]


# ============================================================
# 技术指标
# ============================================================
def _add_indicators_routes(router: APIRouter):
    import pandas as pd
    from indicators.registry import list_indicators, get_indicator

    @router.get("/indicators")
    def list_all():
        return list_indicators()

    @router.get("/indicators/{name}")
    def get_metadata(name: str):
        from indicators.registry import list_indicators as _all, get_indicator
        if name not in _all():
            raise HTTPException(404, f"未知指标: {name}")
        ind = get_indicator(name)
        return {
            "name": ind.name,
            "params": ind.params,
            "param_specs": [s.__dict__ for s in ind.param_specs()],
        }

    @router.post("/indicators/{name}/compute")
    def compute(name: str, payload: dict = Body(...)):
        conn = get_conn()
        stock_code = (payload.get("stock_code") or "").strip()
        params = payload.get("params", {})
        days = int(payload.get("days", 60))
        if not stock_code:
            raise HTTPException(400, "stock_code required")

        # 支持直接输入股票名称：非 6 位数字则按名称解析为代码
        if not (len(stock_code) == 6 and stock_code.isdigit()):
            hit = conn.execute(
                "SELECT stock_code FROM stock_basic WHERE short_name = ? LIMIT 1",
                [stock_code]).fetchone()
            if not hit:
                raise HTTPException(404, f"找不到股票名称「{stock_code}」，请输入 6 位股票代码")
            stock_code = hit[0]

        rows = conn.execute("""
            SELECT trade_date, open, high, low, close, volume
            FROM daily_kline WHERE stock_code=? AND k_type=1
            ORDER BY trade_date DESC LIMIT ?
        """, [stock_code, days]).fetchall()
        if not rows:
            raise HTTPException(404, f"股票 {stock_code} 无 K 线数据")

        df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume"])
        df = df.iloc[::-1].reset_index(drop=True)
        try:
            ind = get_indicator(name, **params)
        except KeyError:
            raise HTTPException(404, f"未知指标: {name}")

        result = ind.compute(df)
        # NaN → None；Timestamp → ISO str
        out = []
        for _, row in result.iterrows():
            r = {}
            for col in result.columns:
                v = row[col]
                if pd.isna(v):
                    r[col] = None
                elif hasattr(v, "isoformat"):
                    r[col] = v.isoformat()
                elif hasattr(v, "item"):
                    # numpy scalar → python native
                    r[col] = v.item()
                else:
                    r[col] = v
            out.append(r)
        return {"rows": out, "name": name, "params": params}


# ============================================================
# 持仓
# ============================================================
def _add_portfolio_routes(router: APIRouter):
    from portfolio.snapshot import PortfolioSnapshot
    from portfolio import sync as portfolio_sync

    @router.get("/portfolio/snapshot")
    def snapshot():
        conn = get_conn()
        snap = PortfolioSnapshot(conn=conn)
        rows = snap.compute()
        return {"rows": [r.to_dict() for r in rows], "total": snap.total()}

    @router.post("/portfolio/sync-prices")
    def sync_prices(codes: List[str] = Body(...)):
        conn = get_conn()
        n = portfolio_sync.sync_prices(conn, codes)
        return {"n_updated": n, "codes": codes}


# ============================================================
# 导出
# ============================================================
def _add_exports_routes(router: APIRouter):
    from exports.markdown import ai_analysis_to_md
    from exports.pdf import ai_analysis_to_pdf
    from exports.excel import watchlist_to_excel

    EXPORT_DIR = Path(__file__).resolve().parent.parent / "data" / "exports"
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    @router.get("/exports/analysis/{aid}/md")
    def export_md(aid: str):
        conn = get_conn()
        try:
            md = ai_analysis_to_md(conn, aid)
        except KeyError:
            raise HTTPException(404, "分析不存在")
        return {"content": md, "analysis_id": aid}

    @router.get("/exports/analysis/{aid}/pdf")
    def export_pdf(aid: str):
        conn = get_conn()
        try:
            path = ai_analysis_to_pdf(conn, aid, str(EXPORT_DIR))
        except KeyError:
            raise HTTPException(404, "分析不存在")
        return {"path": path, "analysis_id": aid}

    @router.get("/exports/watchlist/{group}/excel")
    def export_watchlist(group: str):
        conn = get_conn()
        try:
            path = watchlist_to_excel(conn, group, str(EXPORT_DIR))
        except KeyError:
            raise HTTPException(404, "分组不存在")
        return {"path": path, "group": group}


# ============================================================
# 模型路由
# ============================================================
def _add_router_routes(router: APIRouter):
    from ai import router as ai_router

    @router.get("/router/models")
    def list_models():
        return {
            "config": dict(ai_router.DEFAULT_CONFIG),
            "current": dict(ai_router._CONFIG),
        }

    @router.post("/router/config")
    def update_config(payload: dict = Body(...)):
        ai_router.set_config(payload)
        return {"status": "ok", "config": dict(ai_router._CONFIG)}
