"""Unit tests: arm_spec 'option' product + leverage forcing."""
from __future__ import annotations
import pytest
from vike_trader_app.exec.arm_spec import resolve_arm_spec


def test_option_product_accepted():
    spec = resolve_arm_spec(venue="deribit", environment="DEMO", product="Option",
                            symbol="BTC-27JUN26-100000-C", leverage=1.0, env={})
    assert spec is not None
    assert spec.product == "option"
    assert spec.leverage == 1.0


def test_option_leverage_forced_to_one():
    """Option forces leverage to 1.0 just like spot."""
    spec = resolve_arm_spec(venue="deribit", environment="DEMO", product="Option",
                            symbol="BTC-27JUN26-100000-C", leverage=5.0, env={})
    assert spec.leverage == 1.0


def test_perp_leverage_still_passes_through():
    """The spot/perp arms must be byte-identical: perp with leverage=5 still gives 5.0."""
    spec = resolve_arm_spec(venue="bybit", environment="DEMO", product="Perp",
                            symbol="BTCUSDT", leverage=5.0, env={})
    assert spec.leverage == 5.0


def test_spot_leverage_still_forced():
    """Spot still forces leverage to 1.0."""
    spec = resolve_arm_spec(venue="binance", environment="DEMO", product="Spot",
                            symbol="BTCUSDT", leverage=3.0, env={})
    assert spec.leverage == 1.0


def test_unknown_product_normalizes_to_spot():
    spec = resolve_arm_spec(venue="binance", environment="DEMO", product="futures",
                            symbol="BTCUSDT", leverage=1.0, env={})
    assert spec.product == "spot"
