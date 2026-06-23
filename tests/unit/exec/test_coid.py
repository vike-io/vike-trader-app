"""client_order_id minting: charset-valid, session-prefixed, collision-free across sessions."""

from vike_trader_app.exec.coid import BINANCE_COID_RE, CoidMinter


def test_minted_ids_are_alphanumeric_and_valid_on_all_venues():
    # alphanumeric only, <=32 — the strictest common denominator (OKX clOrdId rejects '-' and caps at 32)
    m = CoidMinter()
    for _ in range(5):
        coid = m.mint()
        assert BINANCE_COID_RE.match(coid), coid
        assert len(coid) <= 32
        assert "-" not in coid and coid.isalnum()


def test_ids_are_monotonic_within_a_session():
    m = CoidMinter(session="abcd1234")
    assert m.mint() == "abcd12340"
    assert m.mint() == "abcd12341"


def test_two_sessions_do_not_collide():
    a = CoidMinter(session="aaaaAAAA")
    b = CoidMinter(session="bbbbBBBB")
    assert a.mint() != b.mint()
