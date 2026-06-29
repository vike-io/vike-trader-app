"""Look-ahead bias detection tests."""

from vike_trader_app.analysis.bias import detect_lookahead, scan_lookahead
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


def _bars(n=6):
    return [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1.0) for i in range(n)]


def _make_clean(view):
    """Honest strategy: decides only from the current bar (ignores the data view)."""

    class S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(1.0)

    return S()


def _make_peek(view):
    """Cheating strategy: peeks at the NEXT bar's close to decide today."""

    class S(Strategy):
        def on_bar(self, bar):
            i = self.index
            if i + 1 < len(view) and view[i + 1].close > bar.close:
                self.buy(1.0)

    return S()


def test_clean_strategy_has_no_lookahead():
    bars = _bars()
    assert detect_lookahead(_make_clean, bars, probe=2) is False
    assert scan_lookahead(_make_clean, bars) == []


def test_peeking_strategy_is_flagged():
    bars = _bars()
    # at the truncation point the peeked future bar disappears -> decision changes
    assert detect_lookahead(_make_peek, bars, probe=3) is True
    assert scan_lookahead(_make_peek, bars) != []
