"""
最简回测示例：N 日动量因子选股
用法：python3 -m analysis.quick_backtest
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import duckdb
from etl.db import get_conn


def run_momentum_backtest(
    rebalance_days: int = 10,   # 回测区间天数
    top_n: int = 30,            # 每期持仓数量
    lookback: int = 20,         # 动量回看天数
):
    """
    简化版动量策略：
    - 每个调仓日（默认 10 天）按过去 20 日收益排序，买入 Top 30
    - 持有 rebalance_days 后再平衡
    - 等权持仓，不扣交易费
    """
    print(f"\n{'='*60}")
    print(f"动量策略回测：rebalance={rebalance_days}d, top={top_n}, lookback={lookback}d")
    print(f"{'='*60}")

    with get_conn(read_only=True) as conn:
        # 拿近一年的 K 线
        df = conn.execute(f"""
            SELECT stock_code, trade_date, close
            FROM daily_kline
            WHERE k_type = 1
              AND trade_date >= CURRENT_DATE - INTERVAL '{rebalance_days + lookback + 365} days'
            ORDER BY stock_code, trade_date
        """).df()

    if df.empty:
        print("(无数据)")
        return

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["stock_code", "trade_date"])

    # 计算每只票过去 N 日收益
    df["ret_lb"] = df.groupby("stock_code")["close"].pct_change(lookback)

    # 交易日列表
    all_dates = sorted(df["trade_date"].unique())
    if len(all_dates) < lookback + rebalance_days:
        print("数据不够")
        return

    # 取调仓日
    rebalance_dates = all_dates[lookback::rebalance_days]
    print(f"调仓日: {len(rebalance_dates)} 个, 范围 {rebalance_dates[0].date()} ~ {rebalance_dates[-1].date()}")

    # 每个调仓日：按动量排序，选 TopN
    portfolio_returns = []
    for i in range(len(rebalance_dates) - 1):
        d = rebalance_dates[i]
        d_next = rebalance_dates[i + 1]
        day_data = df[df["trade_date"] == d].dropna(subset=["ret_lb"])
        if len(day_data) < top_n:
            continue
        # 选 Top N 动量
        picks = day_data.nlargest(top_n, "ret_lb")["stock_code"].tolist()
        # 计算这些票在 [d, d_next) 区间的收益
        window = df[(df["trade_date"] >= d) & (df["trade_date"] < d_next) & (df["stock_code"].isin(picks))]
        period_ret = window.groupby("stock_code").agg(
            p0=("close", "first"), p1=("close", "last")
        )
        period_ret["ret"] = period_ret["p1"] / period_ret["p0"] - 1
        avg_ret = period_ret["ret"].mean()
        if pd.notna(avg_ret):
            portfolio_returns.append({"date": d, "avg_ret": avg_ret})

    if not portfolio_returns:
        print("(无持仓区间)")
        return

    res = pd.DataFrame(portfolio_returns)
    total_ret = (1 + res["avg_ret"]).prod() - 1
    annual_ret = (1 + res["avg_ret"]).mean() * (252 / rebalance_days) - 1
    sharpe = res["avg_ret"].mean() / res["avg_ret"].std() * (252 / rebalance_days) ** 0.5 if res["avg_ret"].std() else 0
    max_dd = ((1 + res["avg_ret"]).cumprod() / (1 + res["avg_ret"]).cumprod().cummax() - 1).min()

    print(f"\n=== 策略表现 ===")
    print(f"  区间: {res['date'].iloc[0].date()} ~ {res['date'].iloc[-1].date()}")
    print(f"  期数: {len(res)}")
    print(f"  累计收益: {total_ret*100:+.2f}%")
    print(f"  年化收益: {annual_ret*100:+.2f}%")
    print(f"  Sharpe:   {sharpe:.2f}")
    print(f"  最大回撤: {max_dd*100:.2f}%")
    print(f"  平均调仓收益: {res['avg_ret'].mean()*100:+.2f}%")


if __name__ == "__main__":
    run_momentum_backtest()
