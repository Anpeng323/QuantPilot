"""Phase 3 沙箱子进程。

由 backtest.sandbox.execute_signal 以子进程方式启动：

    python -m backtest.sandbox_runner <df.pkl> <code.py> <signal.out> <log.json>

职责：
- 在受限命名空间内执行用户策略代码；
- 资源上限（CPU 10s、内存 512MB、墙钟 20s 强杀）；
- 禁网络 / 禁文件系统写 / 最小内置符号；
- 输出布尔信号列表（pickle）与执行日志（json）。
"""
from __future__ import annotations

import sys
import json
import pickle
import builtins
import traceback

try:
    import resource
except ImportError:  # Windows 无 resource 模块
    resource = None

import signal as _signal
import pandas as pd
import numpy as np

from indicators import registry as indicators


class _Runtime:
    """暴露给策略代码的受限工具（runtime 符号）。"""

    def compute_indicator(self, df, name, **params):
        return indicators.get_indicator(name, **params).compute(df)

    def available(self):
        return sorted(indicators.list_indicators())


def _safe_open(*_a, **_k):
    raise PermissionError("沙箱内禁止文件系统写操作")


# 允许保留的内置（最小集合）
_SAFE_BUILTINS = [
    "range", "len", "abs", "min", "max", "sum", "enumerate", "zip", "map",
    "filter", "list", "dict", "set", "tuple", "float", "int", "bool", "str",
    "round", "print", "sorted", "reversed", "any", "all", "divmod", "pow",
    "ord", "chr", "isinstance", "Exception", "ValueError", "TypeError",
    "KeyError", "IndexError", "ZeroDivisionError", "ArithmeticError",
    "OverflowError", "RuntimeError", "NotImplementedError", "AttributeError",
    "NameError", "StopIteration", "next", "iter", "hasattr",
]


def _build_namespace():
    safe = {}
    for name in _SAFE_BUILTINS:
        if hasattr(builtins, name):
            safe[name] = getattr(builtins, name)
    return {
        "__builtins__": safe,
        "pd": pd,
        "np": np,
        "indicators": indicators,
        "runtime": _Runtime(),
    }


def _find_strategy(ns):
    if "strategy" in ns and callable(ns["strategy"]):
        return ns["strategy"]
    # 退而求其次：取本命名空间内最后一个用户定义的函数
    cands = [
        v for k, v in ns.items()
        if k not in ("pd", "np", "indicators", "runtime", "__builtins__")
        and callable(v)
        and getattr(v, "__name__", "") == k
        and getattr(v, "__module__", None) in (None, "__main__", "backtest.sandbox_runner")
    ]
    return cands[-1] if cands else None


def _run(df_path, code_path, signal_path, log_path):
    log = {"ok": False, "message": "", "kind": "exec"}
    # 先打开自身所需的文件句柄（禁用 open 之后再读写会失败）
    df_handle = open(df_path, "rb")
    code_handle = open(code_path, "r", encoding="utf-8")
    signal_handle = open(signal_path, "wb")
    log_handle = open(log_path, "w", encoding="utf-8")
    try:
        # 墙钟强杀时长（可被测试通过 NLBT_ALARM 缩短）
        alarm_sec = 20
        try:
            alarm_sec = int(os.environ.get("NLBT_ALARM", "20"))
        except Exception:
            pass

        # ---- 资源限制 ----
        if resource is not None:
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
            except Exception:
                pass
            try:
                resource.setrlimit(resource.RLIMIT_AS,
                                   (512 * 1024 * 1024, 512 * 1024 * 1024))
            except Exception:
                pass
        _signal.signal(_signal.SIGALRM, lambda *_: _timeout_exit())
        _signal.alarm(alarm_sec)

        # ---- 禁用网络 / 危险模块 / 文件写 ----
        for m in ("socket", "urllib", "urllib.request", "urllib.parse", "http",
                  "http.client", "requests", "subprocess", "shutil", "ftplib",
                  "telnetlib", "smtplib", "poplib", "imaplib", "webbrowser",
                  "os", "pathlib", "ctypes", "multiprocessing"):
            sys.modules[m] = None
        builtins.open = _safe_open

        with df_handle:
            df = pickle.load(df_handle)
        with code_handle:
            code = code_handle.read()

        ns = _build_namespace()
        exec(compile(code, code_path, "exec"), ns)

        func = _find_strategy(ns)
        if func is None:
            raise RuntimeError(
                "未找到策略函数，请定义：def strategy(df, indicators, runtime):"
            )

        result = func(df, indicators, _Runtime())
        if not isinstance(result, pd.Series):
            raise RuntimeError("策略函数必须返回 pandas.Series（布尔信号）")

        sig = result.astype(bool) if result.dtype != bool else result
        sig = sig.reindex(df.index).fillna(False)
        signal_list = [bool(x) for x in sig.tolist()]

        with signal_handle:
            pickle.dump(signal_list, signal_handle)

        log = {"ok": True, "message": "", "kind": "exec", "length": len(signal_list)}
    except Exception as e:  # noqa: BLE001 - 沙箱内统一降级
        log = {
            "ok": False,
            "message": f"{type(e).__name__}: {e}",
            "kind": "exec",
            "traceback": traceback.format_exc()[-2000:],
        }
    finally:
        try:
            json.dump(log, log_handle, ensure_ascii=False)
        finally:
            log_handle.close()


def _timeout_exit():
    sys.stdout.flush()
    sys.stderr.write("sandbox timeout\n")
    sys.exit(137)


if __name__ == "__main__":
    _run(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
