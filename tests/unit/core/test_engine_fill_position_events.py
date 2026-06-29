"""TDD: BacktestEngine fires on_order_filled / on_position_* / on_event from _apply_fill."""
from vike_trader_app.core.model import Bar
from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


class _Rec(Strategy):
    def __init__(self): self.ev = []
    def on_order_filled(self, fill): self.ev.append(("filled", fill.side, round(fill.price, 2), self.position.size))
    def on_position_opened(self, position): self.ev.append(("opened", position.size))
    def on_position_changed(self, position): self.ev.append(("changed", position.size))
    def on_position_closed(self, position): self.ev.append(("closed", position.size))
    def on_event(self, event): self.ev.append(("event", type(event).__name__))


def _bars(n=5):
    return [Bar(ts=t * 60, open=10 + t, high=10 + t, low=10 + t, close=10 + t) for t in range(n)]


def test_open_then_close_events():
    # Bar opens: 10, 11, 12, 13, 14
    # buy at index 0 -> fills at bar 1 open = 11 (next-open semantics)
    # close at index 2 -> fills at bar 3 open = 13
    class S(_Rec):
        def on_bar(self, bar):
            if self.index == 0: self.buy(1.0)
            elif self.index == 2: self.close()
    s = S(); BacktestEngine(_bars(), s).run()
    kinds = [e for e in s.ev if e[0] in ("filled", "opened", "changed", "closed")]
    # entry fill -> filled + opened (pos 1); close fill -> filled + closed (pos 0)
    assert kinds == [("filled", 1, 11.0, 1.0), ("opened", 1.0),
                     ("filled", -1, 13.0, 0.0), ("closed", 0.0)]
    assert ("event", "Fill") in s.ev and ("event", "Position") in s.ev


def test_add_fires_changed():
    class S(_Rec):
        def on_bar(self, bar):
            if self.index in (0, 1): self.buy(1.0)
    s = S(); BacktestEngine(_bars(), s).run()
    seq = [e[0] for e in s.ev if e[0] in ("opened", "changed")]
    assert seq == ["opened", "changed"]   # first buy opens, second adds -> changed


def test_flip_fires_closed_then_opened():
    class S(_Rec):
        def on_bar(self, bar):
            if self.index == 0: self.buy(1.0)
            elif self.index == 2: self.sell(2.0)   # flip long->short
    s = S(); BacktestEngine(_bars(), s).run()
    seq = [e[0] for e in s.ev if e[0] in ("opened", "changed", "closed")]
    assert seq == ["opened", "closed", "opened"]   # open long; flip = close then open short
