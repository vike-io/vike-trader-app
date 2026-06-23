"""CRYPTO_COID_RE is the shared minter charset (Bybit orderLinkId <=36 alnum + -_ is a subset);
BINANCE_COID_RE stays importable as an alias to the same compiled pattern."""

from vike_trader_app.exec.coid import BINANCE_COID_RE, CRYPTO_COID_RE, CoidMinter


def test_binance_alias_is_the_same_object():
    assert BINANCE_COID_RE is CRYPTO_COID_RE


def test_minted_id_is_bybit_order_link_id_valid():
    m = CoidMinter(session="abcd1234")
    coid = m.mint()
    assert CRYPTO_COID_RE.match(coid)
    assert len(coid) <= 36
    # Bybit orderLinkId charset: alphanumerics + - and _
    import re
    assert re.match(r"^[A-Za-z0-9_-]{1,36}$", coid)
