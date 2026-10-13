"""
产业链/概念图谱：基于东方财富行业板块与概念板块构建「股票↔板块」关系边。

- 行业板块：stock_board_industry_name_em / stock_board_industry_cons_em
- 概念板块：stock_board_concept_name_em  / stock_board_concept_cons_em
- 由于每个板块成分股接口约 6s，全量 1000+ 板块需较长时间；
  采用「渐进式夜间回填」：每夜只处理 per_night 个未完成的板块，多夜铺满。
- 关系图：related_stocks() 返回与某股票共享板块最多的相关股（共行业/共概念 = 相关）。

用法：
  python3 -m data_ingest.industry_chain            # 渐进回填（默认每夜 30 个板块）
  python3 -m data_ingest.industry_chain --per-night 20
"""
from __future__ import annotations
import argparse
import time

import akshare as ak
from etl.db import get_conn

# sector_type -> (板块列表函数, 板块成分函数)
BOARDS = {
    "industry": ("stock_board_industry_name_em", "stock_board_industry_cons_em"),
    "concept":  ("stock_board_concept_name_em",  "stock_board_concept_cons_em"),
}


def _ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_chain_meta (
            sector_code  VARCHAR,
            sector_type  VARCHAR,
            sector_name  VARCHAR,
            done         BOOLEAN DEFAULT false,
            updated_at   TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (sector_code, sector_type)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_chain (
            stock_code   VARCHAR,
            sector_type  VARCHAR,
            sector_code  VARCHAR,
            sector_name  VARCHAR,
            PRIMARY KEY (stock_code, sector_type, sector_code)
        )
    """)


def _list_boards(sector_type: str):
    name_fn, _ = BOARDS[sector_type]
    df = getattr(ak, name_fn)()
    return [(str(r["板块代码"]).strip(), str(r["板块名称"]).strip())
            for _, r in df.iterrows()]


def _cons(sector_type: str, code: str):
    _, cons_fn = BOARDS[sector_type]
    df = getattr(ak, cons_fn)(symbol=code)
    return [str(r["代码"]).strip() for _, r in df.iterrows()
            if str(r.get("代码", "")).strip()]


def ensure_boards(sector_type: str):
    """把板块列表登记进 meta（已存在的忽略）。"""
    conn = get_conn()
    for code, name in _list_boards(sector_type):
        if not code:
            continue
        conn.execute("""
            INSERT INTO industry_chain_meta (sector_code, sector_type, sector_name, done)
            VALUES (?, ?, ?, false)
            ON CONFLICT (sector_code, sector_type) DO NOTHING
        """, [code, sector_type, name])


def run_incremental(per_night: int = 30) -> dict:
    """每夜处理最多 per_night 个未完成板块，逐步铺满产业链关系。"""
    conn = get_conn()
    _ensure_tables(conn)
    ensure_boards("industry")
    ensure_boards("concept")

    pending = conn.execute(
        "SELECT sector_code, sector_type, sector_name FROM industry_chain_meta "
        "WHERE done=false ORDER BY sector_type, sector_code"
    ).fetchall()

    processed = 0
    edges = 0
    for code, stype, name in pending:
        try:
            members = _cons(stype, code)
        except Exception as e:
            print(f"  ! {stype}/{name} 成分获取失败: {e}")
            members = []
        for m in members:
            if not m:
                continue
            conn.execute("""
                INSERT INTO industry_chain (stock_code, sector_type, sector_code, sector_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT DO NOTHING
            """, [m, stype, code, name])
        conn.execute(
            "UPDATE industry_chain_meta SET done=true, updated_at=current_timestamp "
            "WHERE sector_code=? AND sector_type=?", [code, stype])
        processed += 1
        edges += len(members)
        if processed >= per_night:
            break

    remain = conn.execute(
        "SELECT COUNT(*) FROM industry_chain_meta WHERE done=false").fetchone()[0]
    print(f"产业链增量：本夜处理 {processed} 个板块，新增关系 {edges} 条（待处理 {remain}）")
    return {"processed": processed, "edges": edges, "remaining": remain}


def related_stocks(stock_code: str, top_n: int = 10) -> list:
    """返回与 stock_code 共享板块最多的相关股票（共行业/共概念 = 相关）。"""
    conn = get_conn()
    secs = conn.execute(
        "SELECT sector_type, sector_code FROM industry_chain WHERE stock_code=?",
        [stock_code]).fetchall()
    if not secs:
        return []
    cnt = {}
    for st, sc in secs:
        rows = conn.execute(
            "SELECT stock_code FROM industry_chain WHERE sector_type=? AND sector_code=?",
            [st, sc]).fetchall()
        for (m,) in rows:
            if m != stock_code:
                cnt[m] = cnt.get(m, 0) + 1
    ranked = sorted(cnt.items(), key=lambda x: -x[1])[:top_n]
    return [{"stock_code": k, "shared_sectors": v} for k, v in ranked]


def sector_members(sector_name: str, limit: int = 200) -> list:
    """返回某板块（名称或代码）的成员股票。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT stock_code FROM industry_chain "
        "WHERE sector_name=? OR sector_code=? ORDER BY stock_code LIMIT ?",
        [sector_name, sector_name, limit]).fetchall()
    return [r[0] for r in rows]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-night", type=int, default=30)
    a = ap.parse_args()
    run_incremental(per_night=a.per_night)
