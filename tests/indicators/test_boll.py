"""
TDD: 布林带 BOLL
- mid = MA(close, n)
- std = close 的 n 日标准差
- up = mid + k * std
- down = mid - k * std
- 默认 n=20, k=2
"""
import pandas as pd
import pytest


class TestBOLL:
    def test_boll_columns(self, sample_kline):
        from indicators.boll import BOLL
        result = BOLL().compute(sample_kline.copy())
        for col in ("boll_mid", "boll_up", "boll_down"):
            assert col in result.columns, f"应该新增 {col} 列"

    def test_boll_known_values(self, sample_kline):
        """第 19 行（period=20 的第 0 个有效值）应等于手动算的 mid/std/up/down"""
        from indicators.boll import BOLL
        result = BOLL(period=20, k=2).compute(sample_kline.copy())
        window = sample_kline["close"].iloc[:20]
        mid = window.mean()
        std = window.std(ddof=1)  # 样本标准差
        up = mid + 2 * std
        down = mid - 2 * std

        assert result["boll_mid"].iloc[19] == pytest.approx(mid, rel=1e-9)
        assert result["boll_up"].iloc[19] == pytest.approx(up, rel=1e-9)
        assert result["boll_down"].iloc[19] == pytest.approx(down, rel=1e-9)

    def test_boll_does_not_mutate_input(self, sample_kline):
        from indicators.boll import BOLL
        original_cols = list(sample_kline.columns)
        BOLL().compute(sample_kline)
        assert list(sample_kline.columns) == original_cols

    def test_boll_up_greater_than_down(self, sample_kline):
        from indicators.boll import BOLL
        result = BOLL().compute(sample_kline.copy())
        mask = result["boll_up"].notna()
        assert (result["boll_up"][mask] >= result["boll_down"][mask]).all()

    def test_boll_short_data_nan(self, sample_short_kline):
        from indicators.boll import BOLL
        result = BOLL(period=20).compute(sample_short_kline.copy())
        assert pd.isna(result["boll_mid"]).all()

    def test_boll_metadata(self):
        from indicators.boll import BOLL
        ind = BOLL(period=10, k=1.5)
        assert ind.name == "boll"
        assert ind.params == {"period": 10, "k": 1.5}
