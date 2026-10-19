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
import signal
import tempfile
import subprocess
import threading
import multiprocessing as mp
from typing import List, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TIMEOUT = 20


class SandboxError(Exception):
    """沙箱执行相关错误。"""

    def __init__(self, message: str, kind: str = "exec"):
        super().__init__(message)
        self.kind = kind  # 'exec' | 'timeout' | 'security'


def execute_signal(code: str, df, timeout: int = DEFAULT_TIMEOUT, capture_debug: bool = False):
    """在沙箱中执行策略代码，返回 (布尔信号列表, 调试日志[, 调试dict])。

    信号列表长度与 df 行数一致、顺序对齐（按位置）。调用方应自行对齐索引。
    capture_debug=True 时额外返回 signal 表达式操作数的末日值 dict（如 {ma5: 12.3, ma20: 11.8}），
    供选股场景展示触发依据；为保持向后兼容，关闭时仍返回 2 元组。
    """
    tmp = tempfile.mkdtemp(prefix="nlbt_")
    df_path = code_path = signal_path = log_path = debug_path = None
    try:
        df_path = os.path.join(tmp, "df.pkl")
        code_path = os.path.join(tmp, "strategy.py")
        signal_path = os.path.join(tmp, "signal.pkl")
        log_path = os.path.join(tmp, "log.json")
        if capture_debug:
            debug_path = os.path.join(tmp, "debug.pkl")

        with open(df_path, "wb") as f:
            pickle.dump(df, f)
        with open(code_path, "w", encoding="utf-8") as f:
            f.write(code)

        argv = [sys.executable, "-m", "backtest.sandbox_runner",
                df_path, code_path, signal_path, log_path]
        if capture_debug:
            argv.append(debug_path)
        proc = subprocess.run(
            argv,
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
        debug = {}
        if capture_debug and debug_path and os.path.exists(debug_path):
            try:
                debug = pickle.load(open(debug_path, "rb"))
            except Exception:
                debug = {}
        if capture_debug:
            return signal_list, log.get("traceback", ""), debug
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


# ---------------------------------------------------------------------------
# 多进程池：选股场景对全市场批量执行时复用 worker 进程（pandas 仅导入一次），
# 避免每支股票都 spawn 子进程导致 pandas 重复导入的严重开销。
# 每个 worker 进程内用 SIGVTALRM 限制单次策略的 CPU 时间，超时即中断。
# ---------------------------------------------------------------------------
_POOL = None
_POOL_LOCK = threading.Lock()


class _PoolTimeout(Exception):
    pass


def _worker_init():
    # 每个 worker 进程只导入一次重型依赖
    import pandas  # noqa: F401
    import numpy  # noqa: F401
    signal.signal(signal.SIGVTALRM, lambda *_: (_ for _ in ()).throw(_PoolTimeout()))


def _worker_exec(payload):
    code, df_bytes, timeout, capture_debug = payload
    from backtest.sandbox_runner import _exec_core
    df = pickle.loads(df_bytes)
    signal.setitimer(signal.ITIMER_VIRTUAL, timeout)
    try:
        signal_list, debug = _exec_core(code, df, capture_debug=capture_debug)
        return pickle.dumps((signal_list, debug))
    finally:
        signal.setitimer(signal.ITIMER_VIRTUAL, 0)


def _get_pool():
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            ctx = mp.get_context("spawn")
            n = min(12, max(2, (os.cpu_count() or 4)))
            _POOL = ctx.Pool(processes=n, initializer=_worker_init)
        return _POOL


def execute_signal_pooled(code: str, df, timeout: int = DEFAULT_TIMEOUT, capture_debug: bool = False):
    """与 execute_signal 等价，但复用多进程池（不反复 spawn 子进程）。

    返回 (signal_list, logs, [debug])：capture_debug=False 时返回二元组（兼容），
    True 时返回三元组。单次执行 CPU 超时抛 SandboxError(kind='timeout')。
    """
    pool = _get_pool()
    df_bytes = pickle.dumps(df)
    try:
        out = pool.apply(_worker_exec, ((code, df_bytes, timeout, capture_debug),))
    except _PoolTimeout:
        raise SandboxError("策略执行超时（可能计算过重），请简化或缩小区间。", kind="timeout")
    signal_list, debug = pickle.loads(out)
    if capture_debug:
        return signal_list, "", debug
    return signal_list, ""
