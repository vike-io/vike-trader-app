"""Strategies can declare indicator overlays for the chart."""

from vike_trader_app.core.strategy import Strategy
from vike_trader_app.ui.dialogs import SmaCross


def test_base_strategy_has_no_overlays():
    assert Strategy().chart_overlays([1.0, 2.0, 3.0]) == {}


def test_sma_cross_overlays_have_expected_keys_and_length():
    closes = [float(i) for i in range(50)]
    overlays = SmaCross().chart_overlays(closes)
    assert set(overlays) == {"SMA10", "SMA30"}
    assert len(overlays["SMA10"]) == len(closes)
    assert len(overlays["SMA30"]) == len(closes)
    # fast SMA becomes non-None earlier than the slow one
    assert overlays["SMA10"][10] is not None
    assert overlays["SMA30"][10] is None
