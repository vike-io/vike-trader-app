from datetime import datetime, timezone

import pytest

from vike_trader_app.data.options.greeks import (
    black_scholes_greeks, enrich_quote, years_to_expiry,
)
from vike_trader_app.data.options.model import OptionQuote


def test_atm_call_greeks_match_reference():
    # S=K=100, t=1y, sigma=0.20, r=0 -> known closed-form values
    delta, gamma, theta, vega = black_scholes_greeks(100.0, 100.0, 1.0, 0.20, "C")
    assert delta == pytest.approx(0.539828, abs=1e-4)
    assert gamma == pytest.approx(0.019848, abs=1e-4)
    assert vega == pytest.approx(0.396953, abs=1e-4)   # per vol-point (sigma/100)
    assert theta == pytest.approx(-0.010875, abs=1e-4)  # per calendar day


def test_put_delta_is_call_delta_minus_one():
    cd, *_ = black_scholes_greeks(100.0, 100.0, 1.0, 0.20, "C")
    pd, *_ = black_scholes_greeks(100.0, 100.0, 1.0, 0.20, "P")
    assert pd == pytest.approx(cd - 1.0, abs=1e-9)


def test_atm_put_greeks_match_reference():
    # at r=0 the put shares the call's gamma/vega/theta; delta = call delta - 1
    delta, gamma, theta, vega = black_scholes_greeks(100.0, 100.0, 1.0, 0.20, "P")
    assert delta == pytest.approx(-0.460172, abs=1e-4)
    assert gamma == pytest.approx(0.019848, abs=1e-4)
    assert vega == pytest.approx(0.396953, abs=1e-4)
    assert theta == pytest.approx(-0.010875, abs=1e-4)


def test_invalid_inputs_return_none():
    assert black_scholes_greeks(100.0, 100.0, 1.0, 0.0, "C") is None   # sigma<=0
    assert black_scholes_greeks(100.0, 100.0, 0.0, 0.2, "C") is None   # t<=0
    assert black_scholes_greeks(None, 100.0, 1.0, 0.2, "C") is None    # missing S


def test_invalid_kind_raises():
    with pytest.raises(ValueError):
        black_scholes_greeks(100.0, 100.0, 1.0, 0.20, "X")


def test_years_to_expiry_30_days():
    exp = "2026-07-02"
    exp_ms = int(datetime(2026, 7, 2, 8, tzinfo=timezone.utc).timestamp() * 1000)
    now = exp_ms - 30 * 86_400 * 1000  # 'now' is synthetically 30 days before expiry
    assert years_to_expiry(exp, now) == pytest.approx(30 / 365.0, abs=1e-6)
    # past expiry clamps to 0
    assert years_to_expiry(exp, exp_ms + 1000) == 0.0


def test_enrich_quote_fills_greeks_when_iv_present():
    q = OptionQuote(strike=100.0, type="C", iv=0.20)
    out = enrich_quote(q, S=100.0, t=1.0)
    assert out.delta == pytest.approx(0.539828, abs=1e-4)
    # no iv -> unchanged
    assert enrich_quote(OptionQuote(strike=100.0, type="C"), S=100.0, t=1.0).delta is None
