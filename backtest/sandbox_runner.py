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
import ast

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
        "_capture_debug": _capture_debug,
        "_last_val": _last_val,
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


def _last_val(v):
    """把指标 Series 取末日标量；非 Series 原样返回；失败返回 None。"""
    if hasattr(v, "iloc"):
        try:
            return float(v.iloc[-1])
        except Exception:
            return None
    return v


def _capture_debug(locals_ns, names):
    """从策略函数局部命名空间里取指定变量名的末日值（供选股展示触发依据）。"""
    out = {}
    for n in names:
        v = locals_ns.get(n)
        if v is None:
            continue
        out[n] = _last_val(v)
    return out


def _instrument_for_debug(code: str):
    """把策略代码改写为：return signal, {"ma5": _last_val(ma5), "ma20": _last_val(ma20), ...}。

    只解析 `signal = <expr>` 这一赋值，提取 <expr> 中引用的变量名（ma5/ma20/rsi 等），
    在 return 处直接引用这些局部变量（它们处于作用域内），用已注入的 _last_val 取末日标量。
    函数返回 (信号Series, {变量名: 末日值})。失败则原样返回（放弃调试信息）。
    """
    try:
        tree = ast.parse(code)
    except Exception:
        return code
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "strategy":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                    t = stmt.targets[0]
                    if isinstance(t, ast.Name) and t.id == "signal":
                        target = stmt.value
                        break
            if target is None:
                # 退化：用 return 表达式里的变量名
                for stmt in node.body:
                    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.expr):
                        target = stmt.value
                        break
            if target is None:
                return code
            names = []
            seen = set()
            for n in ast.walk(target):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                    if n.id in ("df", "indicators", "runtime", "pd", "np"):
                        continue
                    if n.id not in seen:
                        seen.add(n.id)
                        names.append(n.id)
            if not names:
                return code
            # 修改 return 语句，附带调试 dict（直接引用局部变量，避免依赖 locals()）
            for i, stmt in enumerate(node.body):
                if isinstance(stmt, ast.Return):
                    dbg_dict = ast.Dict(
                        keys=[ast.Constant(n) for n in names],
                        values=[
                            ast.Call(
                                func=ast.Name(id="_last_val", ctx=ast.Load()),
                                args=[ast.Name(id=n, ctx=ast.Load())],
                                keywords=[],
                            )
                            for n in names
                        ],
                    )
                    new_ret = ast.Return(
                        value=ast.Tuple(
                            elts=[stmt.value, dbg_dict],
                            ctx=ast.Load(),
                        )
                    )
                    node.body[i] = new_ret
                    break
            try:
                ast.fix_missing_locations(tree)
                return ast.unparse(tree)
            except Exception:
                return code
    return code


def _exec_core(code, df, capture_debug=False):
    """在已加载 pandas 的进程内执行策略，返回 (signal_list, debug_dict)。

    供子进程(_run)与多进程池(选股批量)复用；调用方负责资源/超时限制与异常捕获。
    """
    ns = _build_namespace()
    exec(compile(code, "<strategy>", "exec"), ns)

    func = _find_strategy(ns)
    if func is None:
        raise RuntimeError(
            "未找到策略函数，请定义：def strategy(df, indicators, runtime):"
        )

    run_code = code
    if capture_debug:
        try:
            run_code = _instrument_for_debug(code)
        except Exception:
            run_code = code

    exec(compile(run_code, "<strategy>", "exec"), ns)
    func = _find_strategy(ns)

    result = func(df, indicators, _Runtime())
    # 调试模式返回 (Series, debug_dict)
    debug = {}
    if isinstance(result, tuple) and len(result) == 2:
        result, debug = result
    if not isinstance(result, pd.Series):
        raise RuntimeError("策略函数必须返回 pandas.Series（布尔信号）")

    sig = result.astype(bool) if result.dtype != bool else result
    sig = sig.reindex(df.index).fillna(False)
    signal_list = [bool(x) for x in sig.tolist()]
    return signal_list, debug


def _run(df_path, code_path, signal_path, log_path, debug_path=None):
    log = {"ok": False, "message": "", "kind": "exec"}
    # 先打开自身所需的文件句柄（禁用 open 之后再读写会失败）
    df_handle = open(df_path, "rb")
    code_handle = open(code_path, "r", encoding="utf-8")
    signal_handle = open(signal_path, "wb")
    log_handle = open(log_path, "w", encoding="utf-8")
    # debug 输出也需提前打开，避免 builtins.open 被禁用后无法写入
    debug_handle = open(debug_path, "wb") if debug_path else None
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

        signal_list, debug = _exec_core(code, df, capture_debug=(debug_path is not None))

        with signal_handle:
            pickle.dump(signal_list, signal_handle)

        if debug_handle is not None:
            try:
                pickle.dump(debug or {}, debug_handle)
                debug_handle.close()
            except Exception:
                pass

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
    dbg = sys.argv[5] if len(sys.argv) > 5 else None
    _run(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], debug_path=dbg)
