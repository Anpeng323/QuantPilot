"""
TDD: 移动平均线 MA 指标
- MA(close, n) → close 的 n 日简单移动平均
- 入参：DataFrame（含 close 列）
- 出参：DataFrame 复制 + 新增 ma{n} 列
"""
import pandas as pd
import pytest


class TestMA:
    """测试 MA（移动平均）指标"""

    def test_ma5_basic(self, sample_kline):
        """MA5 = close 的 5 日均值；返回 df 多了 ma5 列"""
        from indicators.ma import MA

        ind = MA(period=5)
        result = ind.compute(sample_kline.copy())

        assert "ma5" in result.columns, "应该新增 ma5 列"
        assert len(result) == len(sample_kline), "行数不变"
        # 前 4 行为 NaN（不足以算 5 日均值）
        assert pd.isna(result["ma5"].iloc[:4]).all(), "前 period-1 行应为 NaN"
        # 第 5 行 = 前 5 行均值
        expected = sample_kline["close"].iloc[:5].mean()
        assert result["ma5"].iloc[4] == pytest.approx(expected, rel=1e-9)
        # 第 6 行 = 第 2~6 行均值
        expected2 = sample_kline["close"].iloc[1:6].mean()
        assert result["ma5"].iloc[5] == pytest.approx(expected2, rel=1e-9)

    def test_ma20_basic(self, sample_kline):
        """MA20 同样规则"""
        from indicators.ma import MA

        result = MA(period=20).compute(sample_kline.copy())
        assert "ma20" in result.columns
        assert pd.isna(result["ma20"].iloc[:19]).all()
        assert result["ma20"].iloc[19] == pytest.approx(
            sample_kline["close"].iloc[:20].mean(), rel=1e-9
        )

    def test_ma_does_not_mutate_input(self, sample_kline):
        """compute 不能原地修改入参 df"""
        from indicators.ma import MA

        original_cols = list(sample_kline.columns)
        original_len = len(sample_kline)
        MA(period=5).compute(sample_kline)
        assert list(sample_kline.columns) == original_cols
        assert len(sample_kline) == original_len

    def test_ma_invalid_period_raises(self, sample_kline):
        """period <= 0 应该报错"""
        from indicators.ma import MA
        with pytest.raises(ValueError):
            MA(period=0).compute(sample_kline)
        with pytest.raises(ValueError):
            MA(period=-1).compute(sample_kline)

    def test_ma_short_data_returns_all_nan(self, sample_short_kline):
        """数据行数 < period 时，所有 ma 值为 NaN"""
        from indicators.ma import MA
        result = MA(period=20).compute(sample_short_kline.copy())
        assert pd.isna(result["ma20"]).all()

    def test_ma_metadata(self):
        from indicators.ma import MA
        ind = MA(period=10)
        assert ind.name == "ma10"
        assert "period" in ind.params
        assert ind.params["period"] == 10
