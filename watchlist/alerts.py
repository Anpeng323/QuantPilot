"""预警：基于最新数据检查条件"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl.db import get_conn


def check_alerts(stock_code: str) -> list:
    """返回该股票的预警列表（v1 简单规则）"""
    alerts = []
    conn = get_conn()
    # 1. 当日跌幅 > 5%
    row = conn.execute("""
        SELECT trade_date, close, change_pct
        FROM daily_kline WHERE stock_code=? AND k_type=1
        ORDER BY trade_date DESC LIMIT 1
    """, [stock_code]).fetchone()
    if row and row[2] is not None and row[2] < -5:
        alerts.append({"level": "high", "type": "big_drop",
                        "message": f"{row[0]} 跌幅 {row[2]:.2f}%"})
    # 2. 量比异常 > 3
    row2 = conn.execute("""
        SELECT trade_date, volume_ratio_20d
        FROM factors_daily WHERE stock_code=?
        ORDER BY trade_date DESC LIMIT 1
    """, [stock_code]).fetchone()
    if row2 and row2[1] is not None and row2[1] > 3:
        alerts.append({"level": "medium", "type": "volume_spike",
                        "message": f"{row2[0]} 量比 {row2[1]:.1f}x（5日均量的{row2[1]:.1f}倍）"})
    # 3. 突破 60 日新高
    row3 = conn.execute("""
        SELECT close FROM daily_kline WHERE stock_code=? AND k_type=1
        ORDER BY trade_date DESC LIMIT 60
    """, [stock_code]).fetchall()
    if len(row3) >= 60:
        latest = row3[0][0]
        high_60 = max(r[0] for r in row3[1:])
        if latest and high_60 and latest >= high_60 * 0.999:
            alerts.append({"level": "low", "type": "break_high",
                            "message": f"突破 60 日新高 {latest:.2f}"})
    return alerts
