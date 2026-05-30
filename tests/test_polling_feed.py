"""PollingBarFeed: emits newly-closed bars, de-dupes by ts, look-ahead-safe.

Driven by an injected ``fetch_latest`` (no network) and an injected clock/sleep,
so the whole loop is deterministic and unit-testable.
"""

from vike_trader_app.core.model import Bar
from vike_trader_app.data.polling_feed import PollingBarFeed


def _bar(ts, c=100.0):
    return Bar(ts=ts, open=c, high=c + 1, low=c - 1, close=c, volume=1.0)


def _feed(latest, clock, **kw):
    return PollingBarFeed(
        "BTCUSDT", "1m", fetch_latest=lambda: latest(), now=lambda: clock["now"], **kw
    )


def test_poll_once_emits_only_closed_bars():
    # 1m bars open at 0, 60_000, 120_000; a bar closes at ts + 60_000.
    bars = [_bar(0), _bar(60_000), _bar(120_000)]
    clock = {"now": 120_001}  # bar@120_000 closes at 180_000 -> still forming
    feed = _feed(lambda: bars, clock)
    assert [b.ts for b in feed.poll_once()] == [0, 60_000]


def test_poll_once_dedupes_already_emitted_bars():
    bars = [_bar(0), _bar(60_000)]
    clock = {"now": 200_000}  # both closed
    feed = _feed(lambda: bars, clock)
    assert [b.ts for b in feed.poll_once()] == [0, 60_000]
    assert feed.poll_once() == []  # nothing new on the second poll


def test_poll_once_emits_a_bar_once_it_closes():
    bars = [_bar(0), _bar(60_000), _bar(120_000)]
    clock = {"now": 120_001}  # bar@120_000 not yet closed
    feed = _feed(lambda: bars, clock)
    assert [b.ts for b in feed.poll_once()] == [0, 60_000]
    clock["now"] = 180_001  # now bar@120_000 has closed
    assert [b.ts for b in feed.poll_once()] == [120_000]


def test_run_emits_each_new_closed_bar_and_sleeps_between_polls():
    # One additional bar closes per poll as the clock advances.
    store = {"bars": [_bar(0)]}
    clock = {"now": 60_001}  # only bar@0 closed so far
    sleeps = []

    def advance(_seconds):
        sleeps.append(_seconds)
        nxt = store["bars"][-1].ts + 60_000
        store["bars"] = store["bars"] + [_bar(nxt)]
        clock["now"] = nxt + 60_001  # the just-added bar is now closed

    feed = _feed(lambda: store["bars"], clock, sleep=advance, poll_seconds=5)
    seen = []
    feed.run(on_bar=seen.append, max_polls=3)

    assert [b.ts for b in seen] == [0, 60_000, 120_000]
    assert sleeps == [5, 5]  # max_polls polls => max_polls-1 sleeps between them


def test_run_stops_when_stop_predicate_is_true():
    feed = _feed(lambda: [_bar(0)], {"now": 200_000}, sleep=lambda s: None)
    seen = []
    feed.run(on_bar=seen.append, stop=lambda: True)  # stop before the first poll
    assert seen == []
