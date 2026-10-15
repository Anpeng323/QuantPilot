"""Phase 3 沙箱父进程：把用户代码放进受限子进程执行，返回布尔信号列表。

安全模型（纵深防御）：
1. backtest.security 的 AST 白名单先拦掉危险语法/导入；
2. 本模块在独立子进程里执行，限制 CPU/内存/墙钟，并禁网络与文件写；
3. 子进程只暴露最小符号（pd / np / indicators / runtime + 极简内置）。

即使生成的代码想逃逸，也无法 import 危险模块、无法 open 文件、无法 __import__。
"""
from __future__ import annotations

import os
import sys
import json
import pickle
import tempfile
import subprocess
from typing import List, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TIMEOUT = 20


class SandboxError(Exception):
    """沙箱执行相关错误。"""

    def __init__(self, message: str, kind: str = "exec"):
        super().__init__(message)
        self.kind = kind  # 'exec' | 'timeout' | 'security'


def execute_signal(code: str, df, timeout: int = DEFAULT_TIMEOUT) -> Tuple[List[bool], str]:
    """在沙箱中执行策略代码，返回 (布尔信号列表, 调试日志)。

    信号列表长度与 df 行数一致、顺序对齐（按位置）。调用方应自行对齐索引。
    """
    tmp = tempfile.mkdtemp(prefix="nlbt_")
    df_path = code_path = signal_path = log_path = None
    try:
        df_path = os.path.join(tmp, "df.pkl")
        code_path = os.path.join(tmp, "strategy.py")
        signal_path = os.path.join(tmp, "signal.pkl")
        log_path = os.path.join(tmp, "log.json")

        with open(df_path, "wb") as f:
            pickle.dump(df, f)
        with open(code_path, "w", encoding="utf-8") as f:
            f.write(code)

        proc = subprocess.run(
            [sys.executable, "-m", "backtest.sandbox_runner",
             df_path, code_path, signal_path, log_path],
            cwd=PROJECT_ROOT,
            timeout=timeout,
            capture_output=True,
            text=True,
        )

        log = {}
        if os.path.exists(log_path):
            try:
                log = json.load(open(log_path, encoding="utf-8"))
            except Exception:
                log = {}

        if not log.get("ok"):
            rc = proc.returncode
            if rc is not None and (rc < 0 or rc == 137):
                # 被信号强杀（CPU 超限 / 墙钟 alarm，exit 137），归为超时
                raise SandboxError(
                    "策略执行超时或资源超限（可能计算过重），请简化或缩小区间。",
                    kind="timeout",
                )
            msg = log.get("message") or (
                proc.stderr.strip()[-1500:] if proc.stderr
                else f"子进程异常退出（code={rc}）"
            )
            raise SandboxError(f"沙箱执行失败：{msg}", kind="exec")

        with open(signal_path, "rb") as f:
            signal_list = pickle.load(f)
        return signal_list, log.get("traceback", "")

    except subprocess.TimeoutExpired:
        raise SandboxError(
            "策略执行超时（可能计算过重），请简化或缩小区间。", kind="timeout"
        )
    finally:
        for p in (df_path, code_path, signal_path, log_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        try:
            if os.path.isdir(tmp):
                os.rmdir(tmp)
        except Exception:
            pass
