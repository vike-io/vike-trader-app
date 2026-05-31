"""Indicator registry: @indicator decorator, REGISTRY, get/list/compute/describe."""

import pytest

from vike_trader_app.core.indicators import base
from vike_trader_app.core.indicators.base import Param, indicator, REGISTRY


def test_indicator_decorator_registers_and_returns_fn():
    @indicator(category="test", inputs=["close"],
               params=[Param("period", "int", 3, 2, 10, 1)], outputs=["thing"])
    def _thing(values, period=3):
        return [sum(values[:period])]

    assert "_thing" in REGISTRY
    spec = REGISTRY["_thing"]
    assert spec.category == "test"
    assert spec.inputs == ["close"]
    assert spec.outputs == ["thing"]
    assert spec.params[0].name == "period" and spec.params[0].default == 3
    assert _thing([1, 2, 3, 4], period=2) == [3]


def test_compute_maps_inputs_and_param_defaults():
    @indicator(name="addup", category="test", inputs=["high", "low"],
               params=[Param("k", "float", 1.0)], outputs=["addup"])
    def _addup(highs, lows, k=1.0):
        return [(h + l) * k for h, l in zip(highs, lows)]

    data = {"high": [2.0, 4.0], "low": [1.0, 1.0]}
    assert base.compute("addup", data) == [3.0, 5.0]
    assert base.compute("addup", data, k=2.0) == [6.0, 10.0]


def test_get_and_list_and_describe():
    @indicator(name="z1", category="catX", inputs=["close"], params=[], outputs=["z1"])
    def _z1(values):
        return values

    assert base.get("z1").name == "z1"
    names = [s.name for s in base.list_indicators(category="catX")]
    assert "z1" in names and all(s.category == "catX" for s in base.list_indicators(category="catX"))
    d = base.describe("z1")
    assert d["name"] == "z1" and d["category"] == "catX" and d["inputs"] == ["close"]
    assert d["params"] == []


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        base.get("does_not_exist_xyz")
