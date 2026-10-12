"""
Pytest configuration and shared fixtures for quant_db test suite.
"""
from __future__ import annotations
import sys
import os
from pathlib import Path

# 把项目根加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import pytest


@pytest.fixture
def sample_kline() -> pd.DataFrame:
    """60 个交易日的样本 K 线（用于指标计算）"""
    import numpy as np
    np.random.seed(42)
    n = 60
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    base = 10.0
    closes = base + np.cumsum(np.random.randn(n) * 0.1)
    highs = closes + np.abs(np.random.randn(n)) * 0.2
    lows = closes - np.abs(np.random.randn(n)) * 0.2
    opens = closes + np.random.randn(n) * 0.1
    volumes = np.random.randint(1000000, 10000000, n)
    return pd.DataFrame({
        "trade_date": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


@pytest.fixture
def sample_short_kline() -> pd.DataFrame:
    """10 个交易日的样本 K 线"""
    import numpy as np
    np.random.seed(123)
    n = 10
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    closes = 10 + np.cumsum(np.random.randn(n) * 0.2)
    return pd.DataFrame({
        "trade_date": dates,
        "open": closes - 0.1,
        "high": closes + 0.2,
        "low": closes - 0.2,
        "close": closes,
        "volume": [1000000] * n,
    })


@pytest.fixture
def tmp_duckdb_path(tmp_path) -> Path:
    db = tmp_path / "test.duckdb"
    return db


@pytest.fixture
def empty_duckdb_conn(tmp_duckdb_path):
    import duckdb
    conn = duckdb.connect(str(tmp_duckdb_path))
    schema = (PROJECT_ROOT / "etl" / "schema.sql").read_text(encoding="utf-8")
    conn.execute(schema)
    yield conn
    conn.close()


@pytest.fixture
def mock_llm(monkeypatch):
    from ai import client as llm_client
    monkeypatch.setattr(llm_client, "PROVIDER", "mock")
    return llm_client


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "integration" in item.nodeid:
            item.add_marker(pytest.mark.integration)
