import pytest

from vike_trader_app.data.options.columns import CHAIN_FIELDS, GREEKS_FIELDS, cell_value, fmt
from vike_trader_app.data.options.model import OptionQuote


def _q():
    return OptionQuote(
        strike=7600.0, type="C", bid=13.6, ask=13.9, last=14.1, mark=13.75,
        iv=0.1784, open_interest=1952, volume=4085,
    )


def test_chain_and_greeks_field_sets_have_known_headers():
    # every field used by either view must have a header + a kind
    from vike_trader_app.data.options.columns import HEADERS, kind
    for f in set(CHAIN_FIELDS) | set(GREEKS_FIELDS):
        assert f in HEADERS and kind(f) in {"px", "pct", "int", "bar", "g"}


def test_direct_fields():
    q = _q()
    assert cell_value("bid", q, 7600.75, 0) == 13.6
    assert cell_value("ask", q, 7600.75, 0) == 13.9
    assert cell_value("ltp", q, 7600.75, 0) == 14.1
    assert cell_value("volume", q, 7600.75, 0) == 4085
    assert cell_value("oi", q, 7600.75, 0) == 1952
    assert cell_value("iv", q, 7600.75, 0) == 0.1784


def test_derived_distance_reldist_pcts():
    q = _q()
    spot = 7600.75
    assert cell_value("distance", q, spot, 0) == pytest.approx(0.75, abs=1e-9)
    assert cell_value("reldist", q, spot, 0) == pytest.approx(0.75 / spot, abs=1e-9)
    assert cell_value("bidpct", q, spot, 0) == pytest.approx(13.6 / spot, abs=1e-9)
    assert cell_value("askpct", q, spot, 0) == pytest.approx(13.9 / spot, abs=1e-9)
    # spread% = (ask-bid)/mark
    assert cell_value("spread", q, spot, 0) == pytest.approx((13.9 - 13.6) / 13.75, abs=1e-9)


def test_theor_uses_black_scholes_and_ann_is_annualized():
    q = _q()
    theor = cell_value("theor", q, 7600.75, 30)   # 30 DTE, iv 17.84% -> a positive BS price
    assert theor is not None and theor > 0
    assert cell_value("annbid", q, 7600.75, 30) == pytest.approx((13.6 / 7600.0) * (365.0 / 30), abs=1e-9)
    assert cell_value("annask", q, 7600.75, 30) == pytest.approx((13.9 / 7600.0) * (365.0 / 30), abs=1e-9)


def test_none_quote_and_missing_context_are_safe():
    assert cell_value("bid", None, 7600.0, 0) is None
    assert cell_value("distance", _q(), None, 0) is None      # no spot
    assert cell_value("reldist", _q(), None, 0) is None
    # theor with no IV -> None (BS needs sigma)
    assert cell_value("theor", OptionQuote(strike=100.0, type="C"), 100.0, 30) is None


def test_fmt_by_kind():
    assert fmt(None, "bid") == "—"
    assert fmt(13.6, "bid") == "13.60"
    assert fmt(0.1784, "iv") == "17.84%"
    assert fmt(4085, "volume") == "4,085"
    assert fmt(0.521, "delta") == "0.521"
