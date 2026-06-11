"""Unit tests for the Qt-free SymbolLinkBus (Phase 3 symbol link groups)."""

from vike_trader_app.ui.linkbus import LINK_COLOR, LINK_GROUPS, SymbolLinkBus


class FakeMember:
    """Duck-typed link member: a group tag + a recording apply_link slot."""

    def __init__(self, group=0):
        self.link_group = group
        self.applied: list[tuple] = []

    def apply_link(self, symbol, interval):
        self.applied.append((symbol, interval))


def test_broadcast_reaches_same_group_excludes_source():
    bus = SymbolLinkBus()
    src = FakeMember(group=1)
    a = FakeMember(group=1)
    b = FakeMember(group=1)
    other = FakeMember(group=2)
    for m in (src, a, b, other):
        bus.add_member(m)

    bus.broadcast(1, src, "ETHUSDT", "4h")
    assert a.applied == [("ETHUSDT", "4h")]
    assert b.applied == [("ETHUSDT", "4h")]
    assert src.applied == []         # source never receives its own broadcast
    assert other.applied == []       # different group untouched


def test_group_zero_is_unlinked():
    bus = SymbolLinkBus()
    a = FakeMember(group=0)
    bus.add_member(a)
    bus.broadcast(0, object(), "BTCUSDT", "1h")
    assert a.applied == []


def test_symbol_only_and_interval_only():
    bus = SymbolLinkBus()
    a = FakeMember(group=3)
    bus.add_member(a)
    bus.broadcast(3, object(), symbol="SOLUSDT")          # watchlist-style: symbol only
    bus.broadcast(3, object(), interval="1d")             # interval-only sync
    assert a.applied == [("SOLUSDT", None), (None, "1d")]


def test_empty_broadcast_is_noop():
    bus = SymbolLinkBus()
    a = FakeMember(group=1)
    bus.add_member(a)
    bus.broadcast(1, object())                            # neither symbol nor interval
    assert a.applied == []


def test_reentrancy_guard_blocks_ping_pong():
    """A member whose apply_link re-broadcasts must not loop back into the same round."""
    bus = SymbolLinkBus()

    class Echo:
        link_group = 1

        def apply_link(self, symbol, interval):
            self.got = (symbol, interval)
            bus.broadcast(1, self, symbol, interval)      # would loop without the guard

    e1, e2 = Echo(), Echo()
    bus.add_member(e1)
    bus.add_member(e2)
    bus.broadcast(1, e1, "BTCUSDT", "1h")
    assert e2.got == ("BTCUSDT", "1h")                    # delivered once, no infinite recursion


def test_recolour_without_reregister():
    bus = SymbolLinkBus()
    a = FakeMember(group=1)
    bus.add_member(a)
    a.link_group = 2                                       # recoloured live
    bus.broadcast(1, object(), "ETHUSDT", "1h")           # group 1 no longer includes a
    assert a.applied == []
    bus.broadcast(2, object(), "ETHUSDT", "1h")
    assert a.applied == [("ETHUSDT", "1h")]


def test_remove_member_stops_delivery():
    bus = SymbolLinkBus()
    a = FakeMember(group=1)
    bus.add_member(a)
    bus.remove_member(a)
    bus.broadcast(1, object(), "ETHUSDT", "1h")
    assert a.applied == []


def test_link_color_table_covers_all_groups():
    assert set(LINK_COLOR) == {gid for gid, _c, _n in LINK_GROUPS}
    assert LINK_COLOR[0] == "#6e7681"          # unlinked = grey


# --- independent interval link channel (MultiCharts-parity: symbol vs timeframe colours) ---

class FakeDualMember:
    """A member carrying BOTH a symbol channel (link_group) and an interval channel."""

    def __init__(self, group=0, interval_group=0):
        self.link_group = group
        self.interval_link_group = interval_group
        self.applied: list[tuple] = []

    def apply_link(self, symbol, interval):
        self.applied.append((symbol, interval))


def test_interval_channel_independent_of_symbol():
    """A window can share the interval colour without sharing the symbol colour, and vice-versa."""
    bus = SymbolLinkBus()
    src = FakeDualMember(group=1, interval_group=2)
    both = FakeDualMember(group=1, interval_group=2)    # shares symbol AND interval
    ivl_only = FakeDualMember(group=3, interval_group=2)  # shares only interval
    sym_only = FakeDualMember(group=1, interval_group=5)  # shares only symbol
    for m in (src, both, ivl_only, sym_only):
        bus.add_member(m)

    bus.broadcast(1, src, "ETHUSDT", "4h", interval_group=2)
    assert both.applied == [("ETHUSDT", "4h")]
    assert ivl_only.applied == [(None, "4h")]            # interval follows, symbol does not
    assert sym_only.applied == [("ETHUSDT", None)]       # symbol follows, interval does not
    assert src.applied == []


def test_interval_unlinked_source_does_not_push_interval():
    bus = SymbolLinkBus()
    src = FakeDualMember(group=1, interval_group=0)      # interval channel = unlinked
    a = FakeDualMember(group=1, interval_group=0)
    bus.add_member(src)
    bus.add_member(a)
    bus.broadcast(1, src, "ETHUSDT", "4h", interval_group=0)
    assert a.applied == [("ETHUSDT", None)]              # symbol synced, interval suppressed


def test_legacy_member_without_interval_attr_follows_symbol_colour_for_interval():
    """Back-compat: a member with only link_group still gets interval on that same colour."""
    bus = SymbolLinkBus()
    a = FakeMember(group=1)                              # no interval_link_group attribute
    bus.add_member(a)
    bus.broadcast(1, object(), "ETHUSDT", "4h")         # legacy call (no interval_group kwarg)
    assert a.applied == [("ETHUSDT", "4h")]


def test_symbol_unlinked_but_interval_linked_routes_interval_only():
    bus = SymbolLinkBus()
    a = FakeDualMember(group=0, interval_group=2)
    bus.add_member(a)
    bus.broadcast(0, object(), "ETHUSDT", "4h", interval_group=2)
    assert a.applied == [(None, "4h")]                  # symbol channel off, interval on
