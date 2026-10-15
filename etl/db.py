"""
统一数据库连接工具
所有表都通过 DuckDB 管理（嵌入式 OLAP 数据库，单文件）
"""
from pathlib import Path
import duckdb

# 数据库文件放在 data/ 目录下
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "quant.duckdb"


def get_conn(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """获取 DuckDB 连接。read_only=True 时用于只读查询，避免文件锁。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH), read_only=read_only)


# 兼容旧库的增量迁移（DDL 幂等：已存在则忽略报错）
_MIGRATIONS = [
    "ALTER TABLE doc ADD COLUMN publish_time TIMESTAMP",
    "ALTER TABLE news_analysis ADD COLUMN detail TEXT",
]


def init_schema() -> None:
    """初始化所有表结构。幂等，可重复执行。"""
    schema_sql = (Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
    conn = get_conn()
    conn.execute(schema_sql)
    for ddl in _MIGRATIONS:
        try:
            conn.execute(ddl)
        except Exception:
            pass
    print(f"[init_schema] 数据库已就绪: {DB_PATH}")


if __name__ == "__main__":
    init_schema()
