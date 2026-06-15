"""Granular intraday sub-bar fill processing (WL "Use Granular Limit/Stop Processing").

When finer (e.g. 1m) bars are available, a coarse bar that spans BOTH a protective stop and a
take-profit limit is drilled into its sub-bars and the fills are resolved in chronological
sub-bar order — so the SL and TP fill in the order they actually occurred. Opt-in: with no
granular data the engine's behavior is unchanged (coarse path, protective stop checked first).
"""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.portfolio import PortfolioEngine, PortfolioStrategy


def _bar(ts, o, h, l, c, v=1.0):
    return Bar(ts=ts, open=o, high=h, low=l, close=c, volume=v)


class _LongWithStopAndTP(PortfolioStrategy):
    """Bar 0: enter long 1 unit of S (fills at bar1 open) with a protective stop=95, and rest a
    take-profit limit_sell @110. The position is open BEFORE the contested bar (bar2), and both the
    armed protective stop and the resting TP go into bar2 — which spans BOTH 95 and 110."""

    def on_bar(self, ts, bars):
        if self.index == 0:
            self._engine.submit("S", +1, 1.0, stop=95.0)
            self._engine.submit_limit("S", -1, 1.0, 110.0)


# Coarse series: bar0 signal, bar1 entry fill (calm open 100), bar2 spans 94..111 (SL vs TP race).
_COARSE = {
    "S": [
        _bar(0, 100, 101, 99, 100),
        _bar(60_000, 100, 101, 99, 100),       # entry fills here at open 100; calm (no SL/TP touch)
        _bar(120_000, 100, 111, 94, 100),      # contested: spans stop 95 AND tp 110
        _bar(180_000, 100, 101, 99, 100),
    ]
}


def test_no_granular_default_unchanged_coarse_stop_wins():
    """No granular data => existing coarse behavior, byte-for-byte (the granular path is never taken).
    On the contested bar2 the protective stop is checked FIRST (top of the step, before this step's
    resting fills), so coarsely the STOP wins: the FIRST trade exits at 95. Documents the coarse
    'which wins' answer: protective stop. (Note: coarsely the un-OCO'd resting TP limit_sell@110 then
    re-fires on the same bar2 high=111 and opens a fresh SHORT — a pre-existing coarse quirk the
    granular path fixes via OCO. We assert only the coarse SL-wins fact here.)"""
    eng = PortfolioEngine(_COARSE, _LongWithStopAndTP(), cash=10_000.0)
    result = eng.run()
    assert result.trades[0].exit_price == 95.0  # protective stop checked first => coarsely wins


def test_granular_dip_first_stop_wins():
    """Sub-bars on bar2 dip to 94 (hits stop 95) BEFORE rallying to 111 (hits TP 110). The STOP
    must fill (exit 95); the TP must NOT; position flat."""
    granular = {
        "S": [
            _bar(120_000, 100, 101, 94, 96),   # dips to 94 -> hits stop 95
            _bar(150_000, 96, 111, 96, 100),   # rallies to 111 -> would hit TP, but stop already out
        ]
    }
    eng = PortfolioEngine(_COARSE, _LongWithStopAndTP(), cash=10_000.0,
                          granular_by_symbol=granular)
    result = eng.run()
    assert len(result.trades) == 1
    assert result.trades[0].exit_price == 95.0  # stop, not TP
    assert eng._sym["S"].pos.size == 0


def test_granular_rally_first_tp_wins():
    """Sub-bars on bar2 rally to 111 (hits TP 110) BEFORE dipping to 94 (hits stop 95). The TP must
    fill (exit 110); the STOP must NOT; position flat."""
    granular = {
        "S": [
            _bar(120_000, 100, 111, 100, 105),  # rallies to 111 -> hits TP 110
            _bar(150_000, 105, 105, 94, 96),    # dips to 94 -> would hit stop, but TP already out
        ]
    }
    eng = PortfolioEngine(_COARSE, _LongWithStopAndTP(), cash=10_000.0,
                          granular_by_symbol=granular)
    result = eng.run()
    assert len(result.trades) == 1
    assert result.trades[0].exit_price == 110.0  # TP, not stop
    assert eng._sym["S"].pos.size == 0


class _GranularLimitEntry(PortfolioStrategy):
    """Bar 0: rest a limit_buy @100 (entry). Coarse bar 1 only dips to 100 part-way through."""

    def on_bar(self, ts, bars):
        if self.index == 0:
            self._engine.submit_limit("S", +1, 1.0, 100.0)


def test_granular_limit_entry_fills_at_dipping_sub_bar():
    """A granular limit ENTRY fills at the sub-bar it first dips to its price (timing + price)."""
    coarse = {
        "S": [
            _bar(0, 105, 106, 104, 105),
            _bar(60_000, 105, 106, 99, 102),   # spans 100 somewhere inside
            _bar(120_000, 102, 103, 101, 102),
        ]
    }
    granular = {
        "S": [
            _bar(60_000, 105, 106, 103, 104),  # sub0: stays above 100
            _bar(90_000, 104, 104, 99, 101),   # sub1: dips to 99 -> crosses 100, fills here
        ]
    }
    eng = PortfolioEngine(coarse, _GranularLimitEntry(), cash=10_000.0,
                          granular_by_symbol=granular)
    eng.run()
    assert eng._sym["S"].pos.size == 1.0
    assert eng._sym["S"].pos.avg_price == 100.0  # filled at the limit price


class _GranularStopNeverHit(PortfolioStrategy):
    def on_bar(self, ts, bars):
        if self.index == 0:
            self._engine.submit_limit("S", +1, 1.0, 50.0)  # way below — never reached


def test_granular_order_that_never_triggers_stays_pending():
    coarse = {
        "S": [
            _bar(0, 105, 106, 104, 105),
            _bar(60_000, 105, 106, 99, 102),
            _bar(120_000, 102, 103, 101, 102),
        ]
    }
    granular = {
        "S": [
            _bar(60_000, 105, 106, 103, 104),
            _bar(90_000, 104, 104, 99, 101),
        ]
    }
    eng = PortfolioEngine(coarse, _GranularStopNeverHit(), cash=10_000.0,
                          granular_by_symbol=granular)
    eng.run()
    assert eng._sym["S"].pos.size == 0
    assert len(eng._sym["S"].pending) == 1  # the limit_buy@50 never triggered, still resting
    assert eng._sym["S"].pending[0].kind == "limit"
