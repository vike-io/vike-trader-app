"""BinanceHmacSigner: known HMAC vector, signature appended last, secret never in repr."""

import hashlib
import hmac

from vike_trader_app.exec.credentials import Credentials
from vike_trader_app.exec.signer import BinanceHmacSigner


def _signer(offset_ms=0):
    creds = Credentials(api_key="KEY", api_secret="SECRET")
    return BinanceHmacSigner(creds, now_ms=lambda: 1_700_000_000_000, offset_ms=offset_ms)


def test_prepare_signs_query_and_sets_header():
    qs, headers = _signer().prepare({"symbol": "BTCUSDT", "side": "BUY"})
    assert headers == {"X-MBX-APIKEY": "KEY"}
    assert qs.startswith("symbol=BTCUSDT&side=BUY&")
    assert "timestamp=1700000000000" in qs
    assert "recvWindow=5000" in qs
    # signature is the LAST param and matches a hand-computed HMAC over everything before it
    body, _, sig = qs.rpartition("&signature=")
    expected = hmac.new(b"SECRET", body.encode(), hashlib.sha256).hexdigest()
    assert sig == expected


def test_offset_is_added_to_timestamp():
    qs, _ = _signer(offset_ms=250).prepare({"a": "1"})
    assert "timestamp=1700000000250" in qs


def test_set_offset_updates_skew():
    s = _signer()
    s.set_offset_ms(-1000)
    qs, _ = s.prepare({"a": "1"})
    assert "timestamp=1699999999000" in qs


def test_secret_never_in_repr_or_str():
    s = _signer()
    assert "SECRET" not in repr(s)
    assert "SECRET" not in str(s)
