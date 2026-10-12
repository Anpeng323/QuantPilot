"""
ETL 通用工具：限流、日志、upsert
"""
from __future__ import annotations
import time
import logging
from functools import wraps
from pathlib import Path
from datetime import datetime
import duckdb
import pandas as pd

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_DIR / f"{datetime.now():%Y-%m-%d}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    fh.flush = lambda: None  # noqa  # already flushes on each emit
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def throttle(seconds: float = 0.1):
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **kw):
            time.sleep(seconds)
            return fn(*a, **kw)
        return wrap
    return deco


def upsert_df(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame, table: str,
              key_cols: list[str], log=None) -> tuple[int, int]:
    """
    Upsert：CREATE TEMP TABLE → DELETE WHERE EXISTS → INSERT。
    使用传入的 conn（不新建连接，避免 DuckDB 文件锁冲突）。
    """
    if df is None or df.empty:
        return 0, 0

    cols = df.columns.tolist()
    col_list = ", ".join(f'"{c}"' for c in cols)

    # CREATE TEMP TABLE 灌入数据
    conn.execute("DROP TABLE IF EXISTS tmp_upsert_df")
    import pyarrow as pa
    arrow_table = pa.Table.from_pandas(df, preserve_index=False)
    conn.register("_tmp_arrow", arrow_table)
    conn.execute(f"CREATE TEMPORARY TABLE tmp_upsert_df AS SELECT {col_list} FROM _tmp_arrow")
    conn.unregister("_tmp_arrow")

    # 统计已存在条数
    key_predicate = " AND ".join([f'tmp_upsert_df."{c}" = "{table}"."{c}"' for c in key_cols])
    already = conn.execute(
        f'SELECT COUNT(*) FROM tmp_upsert_df WHERE EXISTS (SELECT 1 FROM "{table}" WHERE {key_predicate})'
    ).fetchone()[0]
    added = len(df) - already
    updated = already

    # DELETE + INSERT
    conn.execute(f'DELETE FROM "{table}" WHERE EXISTS (SELECT 1 FROM tmp_upsert_df WHERE {key_predicate})')
    conn.execute(f'INSERT INTO "{table}" ({col_list}) SELECT {col_list} FROM tmp_upsert_df')
    conn.execute("DROP TABLE tmp_upsert_df")

    if log:
        log.info(f"  [{table}] +{added} added, ~{updated} updated")
    return added, updated


def job_runner(job_name: str, table_name: str = "-", log=None):
    """装饰器：自动写 update_log 表，记录耗时/状态/异常。"""
    def wrap(fn):
        @wraps(fn)
        def inner(*a, **kw):
            from etl.db import get_conn
            log_ = log or get_logger(job_name)
            t0 = time.time()
            conn = get_conn()
            try:
                result = fn(*a, conn=conn, **kw)
                dur = round(time.time() - t0, 2)
                conn.execute(
                    "INSERT INTO update_log (id, job_name, table_name, status, duration_sec, message) "
                    "VALUES (nextval('seq_log'), ?, ?, 'success', ?, ?)",
                    [job_name, table_name, dur, str(result)[:200]],
                )
                log_.info(f"[{job_name}] 完成, dur={dur}s")
                return result
            except Exception as e:
                dur = round(time.time() - t0, 2)
                try:
                    conn.execute(
                        "INSERT INTO update_log (id, job_name, table_name, status, duration_sec, message) "
                        "VALUES (nextval('seq_log'), ?, ?, 'failed', ?, ?)",
                        [job_name, table_name, dur, repr(e)[:500]],
                    )
                except Exception:
                    pass
                log_.exception(f"[{job_name}] 失败: {e}")
                raise
            finally:
                conn.close()
        return inner
    return wrap
