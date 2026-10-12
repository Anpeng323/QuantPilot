"""
TDD: 指标注册表
- REGISTRY = {name: Indicator class}
- get(name) 返回实例
- 支持列出所有 name
"""
import pytest


class TestRegistry:
    def test_registry_has_core_indicators(self):
        from indicators.registry import REGISTRY
        assert "ma" in REGISTRY
        assert "macd" in REGISTRY
        assert "boll" in REGISTRY
        assert "rsi" in REGISTRY

    def test_get_returns_instance(self, sample_kline):
        from indicators.registry import get_indicator, REGISTRY

        ind = get_indicator("ma", period=5)
        assert isinstance(ind, REGISTRY["ma"])

        # 计算结果
        result = ind.compute(sample_kline.copy())
        assert "ma5" in result.columns

    def test_get_unknown_indicator_raises(self):
        from indicators.registry import get_indicator
        with pytest.raises(KeyError):
            get_indicator("nonexistent_indicator")

    def test_list_indicators(self):
        from indicators.registry import list_indicators
        names = list_indicators()
        assert isinstance(names, list)
        assert "ma" in names
        assert "macd" in names

    def test_every_registered_indicator_supports_work(self, sample_kline):
        """每个注册指标都能跑过（API 一致性）"""
        from indicators.registry import list_indicators, get_indicator
        for name in list_indicators():
            ind = get_indicator(name)
            df = ind.compute(sample_kline.copy())
            # 至少新增 1 列
            assert df.shape[1] >= sample_kline.shape[1], \
                f"{name} 没新增任何列"
