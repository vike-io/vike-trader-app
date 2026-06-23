"""client_order_id minting: charset-valid, session-prefixed, collision-free across sessions."""

from vike_trader_app.exec.coid import BINANCE_COID_RE, CoidMinter


def test_minted_ids_match_binance_charset():
    m = CoidMinter()
    for _ in range(5):
        coid = m.mint()
        assert BINANCE_COID_RE.match(coid), coid
        assert len(coid) <= 36


def test_ids_are_monotonic_within_a_session():
    m = CoidMinter(session="abcd1234")
    assert m.mint() == "abcd1234-0"
    assert m.mint() == "abcd1234-1"


def test_two_sessions_do_not_collide():
    a = CoidMinter(session="aaaaAAAA")
    b = CoidMinter(session="bbbbBBBB")
    assert a.mint().split("-")[0] != b.mint().split("-")[0]
