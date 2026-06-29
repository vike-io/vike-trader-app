from vike_trader_app.core.instrument_id import parse_instrument, format_instrument
from vike_trader_app.core.model import Bar

def test_bare_symbol_uses_default_venue():
    assert parse_instrument("BTCUSDT", default_venue="binance") == ("binance", "BTCUSDT")

def test_qualified_symbol_overrides_default():
    assert parse_instrument("BTCUSDT.BYBIT", default_venue="binance") == ("bybit", "BTCUSDT")

def test_bare_symbol_no_default():
    assert parse_instrument("BTCUSDT") == (None, "BTCUSDT")

def test_default_venue_is_lowercased():
    assert parse_instrument("BTCUSDT", default_venue="BINANCE") == ("binance", "BTCUSDT")

def test_format_roundtrip():
    assert format_instrument("binance", "BTCUSDT") == "BTCUSDT.BINANCE"
    assert format_instrument(None, "BTCUSDT") == "BTCUSDT"

def test_bar_carries_optional_symbol():
    b = Bar(ts=1, open=1, high=2, low=0, close=1)
    assert b.symbol is None
    assert Bar(ts=1, open=1, high=2, low=0, close=1, symbol="BTCUSDT.BINANCE").symbol == "BTCUSDT.BINANCE"
