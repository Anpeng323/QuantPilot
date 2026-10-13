"""
规则回测引擎核心（Phase 1 / T5）。

回测流程（严格防未来函数）：
  load_kline → 指标计算(delay 注入) → 信号求值(T 日收盘) → T+1 撮合 → 成本 → 权益曲线 → 绩效

关键规则：
- 信号在 T 日收盘计算，成交价取 T+1（open/close，含滑点），绝不引用 T+1 及之后数据。
- 持仓为整百股；现金不足按可用资金向下取整到百股；信号转空即平仓（本期不做空/杠杆）。
- 指标预热期（NaN）视为无信号（False），不产生交易。
- 数据缺失率 > 20% 直接中止并提示换区间。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .errors import BacktestError, SignalParseError
from .signal import SignalParser
from .cost import CostConfig, compute_cost
from .metrics import compute_metrics
from .data import load_kline
from .benchmark import load_benchmark


@dataclass
class BacktestRequest:
    stock_code: str
    start: str
    end: str
    signal: str
    initial_cash: float = 100000.0
    fill_price: str = "close"            # 'open' | 'close'
    cost: CostConfig = field(default_factory=CostConfig)
    benchmark: str | None = None         # 指数代码，如 '000300.SH'
    save: bool = False


def _align_benchmark(bench_df, dates, initial_cash):
    """将基准收盘对齐到交易日期序列，前向填充，归一化到 initial_cash。"""
    if bench_df is None or bench_df.empty:
        return [None] * len(dates)
    bmap = dict(zip(
        bench_df["trade_date"].tolist(),
        bench_df["close"].astype(float).tolist(),
    ))
    vals = []
    last = None
    for d in dates:
        if d in bmap:
            last = bmap[d]
        vals.append(last)
    base = next((v for v in vals if v is not None), None)
    if base in (None, 0):
        return [None] * len(dates)
    return [ (v / base * initial_cash) if v is not None else None for v in vals ]


def _run(df: pd.DataFrame, req: BacktestRequest) -> dict:
    if df is None or df.empty or len(df) < 2:
        raise BacktestError("数据不足：该区间无足够行情，请调整起止日期或股票代码")
    df = df.sort_values("trade_date").reset_index(drop=True)

    # 1) 信号求值（注入指标列，惰性计算）
    try:
        parser = SignalParser(req.signal)
        sig = parser.evaluate(df)
    except SignalParseError:
        raise  # 直接上抛 → API 映射 400

    nan_frac = float(sig.isna().mean())
    if nan_frac > 0.2:
        raise BacktestError("信号有效数据不足（缺失率>20%），请缩短区间或检查指标参数")
    data_flag = "部分日期信号缺失（指标预热期）" if nan_frac > 0 else None

    # 2) T+1 撮合主循环
    cash = float(req.initial_cash)
    shares = 0
    avg_cost = 0.0
    buy_fee = 0.0
    trades: list[dict] = []
    equity: list[float] = []

    dates = df["trade_date"].tolist()
    closes = df["close"].astype(float).tolist()
    slip = req.cost.slippage
    fill = req.fill_price if req.fill_price in df.columns else "close"

    for i in range(len(df)):
        # 用 T-1 日信号决定 T 日动作（T+1 成交）
        if i >= 1:
            sig_prev = bool(sig.iloc[i - 1])
            px = float(df[fill].iloc[i])
            if sig_prev and shares == 0:
                exec_px = px * (1.0 + slip)
                qty = int(cash // (exec_px * 100.0)) * 100
                if qty > 0:
                    fee = compute_cost(req.cost, "buy", exec_px, qty)["total"]
                    total = exec_px * qty + fee
                    if total <= cash:
                        cash -= total
                        shares = qty
                        avg_cost = exec_px
                        buy_fee = fee
                        trades.append({
                            "date": str(dates[i]),
                            "side": "buy",
                            "price": round(exec_px, 4),
                            "quantity": qty,
                            "amount": round(exec_px * qty, 2),
                            "fee": round(fee, 4),
                        })
            elif (not sig_prev) and shares > 0:
                exec_px = px * (1.0 - slip)
                fee = compute_cost(req.cost, "sell", exec_px, shares)["total"]
                proceeds = exec_px * shares - fee
                pnl = (exec_px - avg_cost) * shares - fee - buy_fee
                cash += proceeds
                trades.append({
                    "date": str(dates[i]),
                    "side": "sell",
                    "price": round(exec_px, 4),
                    "quantity": shares,
                    "amount": round(exec_px * shares, 2),
                    "fee": round(fee, 4),
                    "pnl": round(pnl, 2),
                })
                shares = 0
                avg_cost = 0.0
                buy_fee = 0.0

        equity.append(cash + shares * float(closes[i]))

    # 3) 基准对齐
    bench_values = [None] * len(dates)
    if req.benchmark:
        try:
            bench_df = load_benchmark(req.benchmark, req.start, req.end)
            bench_values = _align_benchmark(bench_df, dates, req.initial_cash)
        except Exception:
            bench_values = [None] * len(dates)

    # 4) 绩效
    metrics = compute_metrics(equity, bench_values, trades, rf=0.025)

    equity_curve = [
        {
            "date": str(dates[i]),
            "value": round(equity[i], 2),
            "benchmark_value": (
                round(bench_values[i], 2)
                if (bench_values and bench_values[i] is not None) else None
            ),
        }
        for i in range(len(dates))
    ]

    return {
        "stock_code": req.stock_code,
        "signal": req.signal,
        "trades": trades,
        "equity_curve": equity_curve,
        "metrics": metrics,
        "data_flag": data_flag,
    }


def run_backtest_df(df: pd.DataFrame, req: BacktestRequest) -> dict:
    """直接对给定 DataFrame 回测（供测试/复用，不触达数据库）。"""
    return _run(df, req)


def run_backtest(req: BacktestRequest) -> dict:
    """从数据库加载行情并回测。"""
    df = load_kline(req.stock_code, req.start, req.end)
    return _run(df, req)
