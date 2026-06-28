from vike_trader_app.core.model import Bar
from vike_trader_app.exec.conditionals import ConditionalBook


def _bar(ts, o, h, l, c): return Bar(ts=ts, open=o, high=h, low=l, close=c, volume=1.0)


def test_buy_stop_fires_when_high_crosses():
    b = ConditionalBook(); b.add_stop(side=+1, size=2.0, price=110.0)
    assert b.check(_bar(1, 100, 105, 99, 102)) == []     # high 105 < 110 -> no fire, stays
    assert len(b) == 1
    fired = b.check(_bar(2, 106, 111, 105, 109))         # high 111 >= 110 -> fire
    assert len(fired) == 1 and fired[0].side == +1 and fired[0].size == 2.0
    assert len(b) == 0                                    # fire-once: removed


def test_sell_stop_fires_when_low_crosses():
    b = ConditionalBook(); b.add_stop(side=-1, size=1.0, price=90.0)
    assert b.check(_bar(1, 100, 101, 95, 98)) == []      # low 95 > 90 -> no fire
    fired = b.check(_bar(2, 96, 97, 89, 91))             # low 89 <= 90 -> fire
    assert len(fired) == 1 and fired[0].side == -1


def test_trailing_ratchets_then_fires_on_retrace():
    # sell-trailing (protects a long): trigger = extreme - trail; ratchets extreme up on new highs.
    b = ConditionalBook(); b.add_trailing(side=-1, size=1.0, trail=5.0, extreme=100.0)
    assert b.check(_bar(1, 100, 108, 99, 107)) == []     # no retrace; extreme ratchets to 108
    # now trigger = 108 - 5 = 103; a bar with low <= 103 fires
    fired = b.check(_bar(2, 106, 107, 102, 104))         # low 102 <= 103 -> fire
    assert len(fired) == 1 and fired[0].side == -1
    assert len(b) == 0


def test_clear_empties_book():
    b = ConditionalBook(); b.add_stop(side=+1, size=1.0, price=110.0)
    b.clear(); assert len(b) == 0 and b.check(_bar(1, 100, 200, 50, 150)) == []
