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


def test_legacy_ta_imports_still_work_and_match():
    from vike_trader_app.core.indicators import ta
    from vike_trader_app.core.indicators.base import REGISTRY

    legacy_names = ["sma", "ema", "wma", "rsi", "macd", "stochastic", "cci", "williams_r",
                    "roc", "adx", "obv", "vwap", "atr", "true_range", "bollinger",
                    "keltner", "donchian"]
    for name in legacy_names:
        assert hasattr(ta, name), f"ta.{name} missing"
        assert name in REGISTRY, f"{name} not registered"
    assert callable(ta.expand) and callable(ta.from_talib)
    import vike_trader_app.core.indicators.base as base
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert ta.sma(vals, 3) == base.compute("sma", {"close": vals}, period=3)


def test_compute_multiline_indicator():
    import vike_trader_app.core.indicators.base as base
    closes = [float(i) for i in range(40)]
    data = {"high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": closes}
    upper, mid, lower = base.compute("bollinger", data, period=20, k=2.0)
    assert len(upper) == len(mid) == len(lower) == 40
