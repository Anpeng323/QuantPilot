"""
DuckDB 交互式查询 CLI
用法：
  python3 -m analysis.query                  # 进入 REPL
  python3 -m analysis.query -e "SELECT ..."  # 执行单条 SQL
  python3 -m analysis.query -f file.sql      # 执行 SQL 文件
"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb
from etl.db import DB_PATH


def execute(sql: str, conn: duckdb.DuckDBPyConnection):
    """执行 SQL，自动判断 SELECT/DDL。"""
    sql_stripped = sql.strip().rstrip(";")
    try:
        result = conn.execute(sql_stripped)
        if result.description:
            cols = [d[0] for d in result.description]
            rows = result.fetchall()
            if not rows:
                print("(0 rows)")
                return
            # 表格打印
            widths = [max(len(c), max((len(str(r[i])) for r in rows), default=0))
                      for i, c in enumerate(cols)]
            print(" | ".join(c.ljust(w) for c, w in zip(cols, widths)))
            print("-+-".join("-" * w for w in widths))
            for r in rows:
                print(" | ".join(str(v).ljust(w) for v, w in zip(r, widths)))
            print(f"\n({len(rows)} rows)")
        else:
            print(f"OK. rows affected: {result.fetchone()}")
    except Exception as e:
        print(f"[ERROR] {e}")


def repl():
    print(f"📊 DuckDB REPL — {DB_PATH}")
    print("输入 SQL（; 结尾），空行退出。")
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    while True:
        try:
            line = input("sql> ")
        except (EOFError, KeyboardInterrupt):
            break
        line = line.strip()
        if not line:
            break
        if ";" in line:
            execute(line, conn)
        else:
            # 多行模式
            buf = [line]
            while True:
                more = input("..> ")
                buf.append(more)
                if ";" in more:
                    break
            execute("\n".join(buf), conn)
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-e", "--execute", help="执行单条 SQL")
    parser.add_argument("-f", "--file", help="执行 SQL 文件")
    parser.add_argument("--readonly", action="store_true", default=True)
    args = parser.parse_args()

    if args.execute:
        with duckdb.connect(str(DB_PATH), read_only=True) as conn:
            execute(args.execute, conn)
    elif args.file:
        sql = Path(args.file).read_text(encoding="utf-8")
        with duckdb.connect(str(DB_PATH), read_only=True) as conn:
            for stmt in sql.split(";"):
                if stmt.strip() and not all(line.strip().startswith("--") or not line.strip()
                                            for line in stmt.split("\n")):
                    execute(stmt, conn)
    else:
        repl()


if __name__ == "__main__":
    main()
