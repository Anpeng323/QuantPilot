"""
TDD: 指标注册表应包含 4 个新增指标 (kdj/arbr/adx/stochrsi)，
且能通过 get_indicator 实例化并可计算。
"""
import pandas as pd
import pytest


class TestRegistryNew:
    def test_new_indicators_registered(self):
        from indicators.registry import list_indicators, get_indicator
        for name in ("kdj", "arbr", "adx", "stochrsi"):
            assert name in list_indicators(), f"{name} 未注册"
            ind = get_indicator(name)
            assert ind is not None

    def test_get_unknown_raises(self):
        from indicators.registry import get_indicator
        with pytest.raises(KeyError):
            get_indicator("not_a_real_indicator")

    def test_registry_can_compute(self, sample_kline):
        from indicators.registry import get_indicator
        for name, key_cols in (
            ("kdj", ("kdj_k", "kdj_d", "kdj_j")),
            ("arbr", ("ar", "br")),
            ("adx", ("adx", "plus_di", "minus_di")),
            ("stochrsi", ("stochrsi", "stochrsi_d")),
        ):
            res = get_indicator(name).compute(sample_kline.copy())
            for c in key_cols:
                assert c in res.columns, f"{name} 缺少列 {c}"
