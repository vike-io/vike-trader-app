"""Signer.prepare widens with keyword method/path defaults; BinanceHmacSigner ignores them and
returns the IDENTICAL 2-tuple whether or not the new keywords are passed (byte-identity)."""

from vike_trader_app.exec.credentials import Credentials
from vike_trader_app.exec.signer import BinanceHmacSigner, PreparedRequest


def _signer():
    creds = Credentials(api_key="KEY", api_secret="SECRET")
    return BinanceHmacSigner(creds, now_ms=lambda: 1_700_000_000_000)


def test_prepared_request_defaults():
    pr = PreparedRequest()
    assert pr.query == ""
    assert pr.body is None
    assert pr.headers == {}


def test_binance_prepare_ignores_new_keywords():
    s = _signer()
    legacy = s.prepare({"symbol": "BTCUSDT", "side": "BUY"})
    widened = s.prepare({"symbol": "BTCUSDT", "side": "BUY"}, method="POST", path="/api/v3/order")
    assert legacy == widened
    # still a 2-tuple (query, headers) — the transport's legacy branch relies on this
    assert isinstance(legacy, tuple) and len(legacy) == 2
