"""
行业数据接入（v1.2）：申万行业分类，adata 免费源，无需 token。
写入 stock_industry（stock_code, sw_code, industry_name, industry_type, source）。

改进（v1.2）：
  - 抓取失败/无数据时写入 industry_fetch_skip 标记表，后续增量运行直接跳过，
    避免对已退市/源端缺失的股票反复请求（旧版每次重跑都会卡在它们身上）。
  - 抓取带限流间隔 + 失败指数退避重试，降低 adata 限流概率。

用法：
  python3 -m data_ingest.industry                  # 全量增量（已有/已标记跳过则跳过）
  python3 -m data_ingest.industry --limit 50       # 前 50 只验证
  python3 -m data_ingest.industry --force          # 忽略已有数据，全量重抓
  python3 -m data_ingest.industry --reset-skip     # 清除失败标记后增量跑
"""
import sys
import time
import argparse
from etl.db import get_conn
import adata

# 抓取失败/无数据的股票标记表（主键 stock_code，避免重复请求）
SKIP_TABLE = "industry_fetch_skip"


def ensure_skip_table(conn) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {SKIP_TABLE} (
            stock_code VARCHAR PRIMARY KEY,
            reason VARCHAR,
            tried_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def fetch_industry(stock_code: str, retries: int = 2, retry_delay: float = 0.5) -> list:
    """返回该股票的申万行业记录列表（含一级/二级）。

    失败或空结果时按指数退避重试，仍失败则返回 []。
    """
    for attempt in range(retries + 1):
        try:
            df = adata.stock.info.get_industry_sw(stock_code)
        except Exception:
            df = None
        if df is not None and len(df) > 0:
            return df.to_dict("records")
        if attempt < retries:
            time.sleep(retry_delay * (2 ** attempt))
    return []


def save_industry(stock_code: str, rows: list) -> int:
    conn = get_conn()
    # 先清该股票旧数据，再插入（整批刷新，避免重复）
    conn.execute("DELETE FROM stock_industry WHERE stock_code=?", [stock_code])
    n = 0
    for r in rows:
        try:
            conn.execute(
                """INSERT INTO stock_industry
                   (stock_code, sw_code, industry_name, industry_type, source)
                   VALUES (?, ?, ?, ?, ?)""",
                [stock_code, r.get("sw_code"), r.get("industry_name"),
                 r.get("industry_type"), r.get("source")],
            )
            n += 1
        except Exception:
            pass
    return n


def run(limit: int = None, force: bool = False, reset_skip: bool = False,
        rate_limit: float = 0.1, retries: int = 2) -> dict:
    conn = get_conn()
    ensure_skip_table(conn)
    if reset_skip:
        conn.execute(f"DELETE FROM {SKIP_TABLE}")

    codes = [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_basic ORDER BY stock_code"
    ).fetchall()]
    if limit:
        codes = codes[:limit]
    total_rows = 0
    ok = 0
    skipped_existing = 0
    skipped_marked = 0
    failed = 0
    skip_codes = {r[0] for r in conn.execute(
        f"SELECT stock_code FROM {SKIP_TABLE}").fetchall()}

    for i, code in enumerate(codes, 1):
        # 增量：已有数据则跳过（除非 --force）
        if not force and conn.execute(
            "SELECT 1 FROM stock_industry WHERE stock_code=?", [code]
        ).fetchone():
            skipped_existing += 1
            continue
        # 已标记抓取失败/无数据，跳过（除非 --force/--reset-skip）
        if not force and code in skip_codes:
            skipped_marked += 1
            continue
        rows = fetch_industry(code, retries=retries)
        if rows:
            total_rows += save_industry(code, rows)
            ok += 1
        else:
            try:
                conn.execute(
                    f"INSERT INTO {SKIP_TABLE} (stock_code, reason) "
                    f"VALUES (?, 'empty') ON CONFLICT(stock_code) DO NOTHING",
                    [code],
                )
            except Exception:
                pass
            failed += 1
        # 限流间隔，避免触发 adata 频率限制
        if rate_limit:
            time.sleep(rate_limit)
        if i % 200 == 0:
            print(f"  ...{i}/{len(codes)} 新增{ok} 已存在跳过{skipped_existing} "
                  f"标记跳过{skipped_marked} 失败{failed} 记录{total_rows}")
    print(f"行业接入完成：新增 {ok} 只，已存在跳过 {skipped_existing} 只，"
          f"历史标记跳过 {skipped_marked} 只，本次失败 {failed} 只，"
          f"共 {total_rows} 条新记录")
    return {"stocks": len(codes), "added": ok, "skipped_existing": skipped_existing,
            "skipped_marked": skipped_marked, "failed": failed, "rows": total_rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="忽略已有数据，全量重抓")
    ap.add_argument("--reset-skip", action="store_true",
                    help="清除抓取失败标记后增量跑（用于修正历史漏抓）")
    ap.add_argument("--retries", type=int, default=2, help="单只抓取失败重试次数")
    ap.add_argument("--rate-limit", type=float, default=0.1, help="每只之间休眠秒数（限流）")
    a = ap.parse_args()
    run(limit=a.limit, force=a.force, reset_skip=a.reset_skip, retries=a.retries,
        rate_limit=a.rate_limit)

