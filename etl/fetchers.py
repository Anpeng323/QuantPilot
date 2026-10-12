"""
独立 fetcher 集合。
不依赖 adata 内部封装的 east_market（东方财富接口现在经常不可达），
直接对接新浪、腾讯等稳定的源。
"""
from __future__ import annotations
import time
import requests
import pandas as pd
from datetime import datetime
from typing import Optional

# K线粒度：scale 对应秒数
# 5min=300, 15min=900, 30min=1800, 60min=3600, 日=86400/240? 不对，日K=240 分钟
SCALE_MAP = {
    5: 5,        # 5 分钟（新浪是 1/5/15/30/60）
    15: 15,
    30: 30,
    60: 60,
    101: 240,    # 日 K = 240 分钟（每个交易日 4 小时）
    102: 1680,   # 周 K = 240*7
    103: 7200,   # 月 K = 240*30
}


def _market_prefix(stock_code: str) -> str:
    return "sh" if stock_code.startswith("6") else "sz" if stock_code.startswith(("0", "3")) else "bj"


def fetch_sina_kline(stock_code: str, k_type: int = 1, datalen: int = 1500) -> pd.DataFrame:
    """
    新浪历史 K 线。datalen ≤ 1500
    k_type: 1/日 2/周 3/月 5/5min 15/15min 30/30min 60/60min
    返回 DataFrame: [stock_code, trade_date, trade_time, open, high, low, close, volume, amount, k_type]
    """
    scale = SCALE_MAP.get(k_type, 240)
    market = _market_prefix(stock_code)
    symbol = f"{market}{stock_code}"
    url = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
    params = {"symbol": symbol, "scale": scale, "ma": "no", "datalen": min(datalen, 1500)}

    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
    except Exception:
        return pd.DataFrame()

    if not isinstance(data, list) or not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)
    df = df.rename(columns={"day": "trade_time", "open": "open", "high": "high",
                            "low": "low", "close": "close", "volume": "volume"})
    df["stock_code"] = stock_code
    df["open"]   = pd.to_numeric(df["open"],   errors="coerce")
    df["high"]   = pd.to_numeric(df["high"],   errors="coerce")
    df["low"]    = pd.to_numeric(df["low"],    errors="coerce")
    df["close"]  = pd.to_numeric(df["close"],  errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
    # 新浪分钟线带 amount
    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    else:
        df["amount"] = (df["close"] * df["volume"]).astype(float)

    # trade_date 和 trade_time 拆分
    if " " in df["trade_time"].iloc[0]:
        df["trade_time"] = pd.to_datetime(df["trade_time"], errors="coerce")
        df["trade_date"] = df["trade_time"].dt.date
    else:
        df["trade_date"] = pd.to_datetime(df["trade_time"], errors="coerce").dt.date
        df["trade_time"] = pd.to_datetime(df["trade_time"]).dt.strftime("%Y-%m-%d 00:00:00")

    df["k_type"] = k_type
    # 计算前收、涨跌
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["pre_close"] = df["close"].shift(1)
    df["change"]    = df["close"] - df["pre_close"]
    df["change_pct"] = df["change"] / df["pre_close"] * 100
    df["adjust_type"] = 0  # 新浪不复权

    return df[["stock_code", "trade_date", "trade_time", "k_type", "adjust_type",
               "open", "high", "low", "close", "pre_close",
               "change", "change_pct", "volume", "amount"]]


def fetch_qq_quote(stock_code: str) -> dict:
    """腾讯实时行情（1只）"""
    market = _market_prefix(stock_code)
    url = f"https://qt.gtimg.cn/q={market}{stock_code}"
    r = requests.get(url, timeout=5)
    if r.status_code != 200 or "=" not in r.text:
        return {}
    parts = r.text.split("~")
    if len(parts) < 35:
        return {}
    return {
        "stock_code":   stock_code,
        "short_name":   parts[1],
        "price":        float(parts[3]),
        "prev_close":   float(parts[4]),
        "open":         float(parts[5]),
        "volume":       int(parts[6]) * 100,    # 手 → 股
        "amount":       float(parts[7]) * 10000, # 万 → 元
        "high":         float(parts[33]) if len(parts) > 33 else None,
        "low":          float(parts[34]) if len(parts) > 34 else None,
        "change_pct":   float(parts[32]) if len(parts) > 32 else None,
        "trade_time":   parts[30] if len(parts) > 30 else None,
    }


def fetch_qq_quotes_batch(stock_codes: list[str]) -> pd.DataFrame:
    """腾讯批量实时行情"""
    if not stock_codes:
        return pd.DataFrame()
    qs = ",".join([f"{_market_prefix(c)}{c}" for c in stock_codes])
    url = f"https://qt.gtimg.cn/q={qs}"
    r = requests.get(url, timeout=10)
    rows = []
    for line in r.text.split(";"):
        if "=" not in line or len(line) < 8:
            continue
        parts = line.split("~")
        if len(parts) < 35:
            continue
        try:
            code = parts[2]
            rows.append({
                "stock_code": code,
                "short_name": parts[1],
                "price":      float(parts[3]),
                "prev_close": float(parts[4]),
                "open":       float(parts[5]),
                "volume":     int(parts[6]) * 100,
                "amount":     float(parts[7]) * 10000,
                "high":       float(parts[33]) if len(parts) > 33 else None,
                "low":        float(parts[34]) if len(parts) > 34 else None,
                "change_pct": float(parts[32]) if len(parts) > 32 else None,
            })
        except (ValueError, IndexError):
            continue
    return pd.DataFrame(rows)


def fetch_sina_index_kline(index_code: str, k_type: int = 5, datalen: int = 100) -> pd.DataFrame:
    """
    新浪指数 K 线（含分钟）。index_code 如 'sh000001' 'sz399006'
    返回 DataFrame: [index_code, trade_time, trade_date, k_type, open, high, low, close, volume, amount]
    """
    scale = SCALE_MAP.get(k_type, 5)
    url = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
    params = {"symbol": index_code, "scale": scale, "ma": "no", "datalen": min(datalen, 1500)}
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
    except Exception:
        return pd.DataFrame()
    if not isinstance(data, list) or not data:
        return pd.DataFrame()
    df = pd.DataFrame(data).rename(columns={"day": "trade_time", "open": "open", "high": "high",
                                            "low": "low", "close": "close", "volume": "volume"})
    df["open"]   = pd.to_numeric(df["open"],   errors="coerce")
    df["high"]   = pd.to_numeric(df["high"],   errors="coerce")
    df["low"]    = pd.to_numeric(df["low"],    errors="coerce")
    df["close"]  = pd.to_numeric(df["close"],  errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    else:
        df["amount"] = (df["close"] * df["volume"]).astype(float)
    # 新浪分钟线 trade_time 形如 "2026-10-12 14:55:00"
    if " " in str(df["trade_time"].iloc[0]):
        df["trade_time"] = pd.to_datetime(df["trade_time"], errors="coerce")
        df["trade_date"] = df["trade_time"].dt.date
    else:
        df["trade_date"] = pd.to_datetime(df["trade_time"], errors="coerce").dt.date
        df["trade_time"] = pd.to_datetime(df["trade_time"]).dt.strftime("%Y-%m-%d 00:00:00")
    df["k_type"] = k_type
    df["index_code"] = index_code
    df = df.sort_values("trade_time").reset_index(drop=True)
    return df[["index_code", "trade_time", "trade_date", "k_type", "open", "high", "low", "close", "volume", "amount"]]


def fetch_sina_finance_index(index_code: str, datalen: int = 1500) -> pd.DataFrame:
    """新浪指数历史 K 线。index_code 如 'sh000001' 'sz399006'"""
    url = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
    params = {"symbol": index_code, "scale": 240, "ma": "no", "datalen": min(datalen, 1500)}
    r = requests.get(url, params=params, timeout=10)
    try:
        data = r.json()
    except Exception:
        return pd.DataFrame()
    if not isinstance(data, list) or not data:
        return pd.DataFrame()
    df = pd.DataFrame(data).rename(columns={"day": "trade_date"})
    df["open"]   = pd.to_numeric(df["open"],   errors="coerce")
    df["high"]   = pd.to_numeric(df["high"],   errors="coerce")
    df["low"]    = pd.to_numeric(df["low"],    errors="coerce")
    df["close"]  = pd.to_numeric(df["close"],  errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
    df["amount"] = (df["close"] * df["volume"]).astype(float)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["change_pct"] = df["close"].pct_change() * 100
    df["index_code"] = index_code
    return df[["index_code", "trade_date", "open", "high", "low", "close",
               "volume", "amount", "change_pct"]]


if __name__ == "__main__":
    # 烟雾测试
    print("\n测试1: 平安银行日 K (000001)")
    df = fetch_sina_kline("000001", k_type=1, datalen=10)
    print(df.tail(3))

    print("\n测试2: 30min K")
    df2 = fetch_sina_kline("000001", k_type=30, datalen=5)
    print(df2.tail(2))

    print("\n测试3: 实时行情")
    q = fetch_qq_quote("000001")
    print(q)

    print("\n测试4: 指数K线")
    df3 = fetch_sina_finance_index("sh000001", datalen=5)
    print(df3.tail(2))
