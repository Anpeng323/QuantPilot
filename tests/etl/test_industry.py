"""etl.industry 板块映射（四档优先级 + 进程内缓存）单元测试。"""
from __future__ import annotations

from unittest import mock

import etl.industry as ei
from etl.industry import get_board_map, enrich_with_board


def _reset_cache():
    ei._cache["map"] = None
    ei._cache["ts"] = 0.0


def _fake_conn(rows):
    fake = mock.MagicMock()
    fake.execute.return_value.fetchall.return_value = rows
    return fake


def test_get_board_map_priority():
    rows = [
        ("A", "银行", "申万一级"),
        ("A", "股份制银行Ⅱ", "申万二级"),   # 申万一级已存在，忽略二级
        ("B", "白酒Ⅱ", "申万二级"),         # 无申万一级，退回申万二级
        ("C", "制造业", "一级"),             # 仅国民经济分类：取更具体的二级
        ("C", "造纸和纸制品业", "二级"),
        (None, "xx", "申万一级"),            # 空 code 跳过
    ]
    with mock.patch("etl.industry.get_conn", return_value=_fake_conn(rows)):
        _reset_cache()
        m = get_board_map()
    assert m == {"A": "银行", "B": "白酒Ⅱ", "C": "造纸和纸制品业"}


def test_enrich_with_board_adds_field():
    rows = [{"stock_code": "A"}, {"stock_code": "X"}]
    rows_with_blank = [("A", "食品饮料", "申万一级")]
    with mock.patch("etl.industry.get_conn", return_value=_fake_conn(rows_with_blank)):
        _reset_cache()
        enrich_with_board(rows)
    assert rows[0]["industry"] == "食品饮料"
    assert rows[1]["industry"] == ""        # 无行业数据 → 空串


def test_get_board_map_cache_hit_avoids_db():
    rows = [("A", "食品饮料", "申万一级")]
    with mock.patch("etl.industry.get_conn", return_value=_fake_conn(rows)) as m:
        _reset_cache()
        get_board_map()                      # 首次：查库
        get_board_map()                      # 二次：应命中缓存
        assert m.call_count == 1
