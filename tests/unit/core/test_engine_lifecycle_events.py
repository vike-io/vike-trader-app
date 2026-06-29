from vike_trader_app.core.model import Bar
from vike_trader_app.core.engine import SingleSymbolEngine
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


def _bars(n=3):
    return [Bar(ts=t * 60, open=100, high=100, low=100, close=100) for t in range(n)]


def test_start_stop_and_submitted():
    class S(Strategy):
        def __init__(self): self.ev = []
        def on_start(self): self.ev.append("start")
        def on_stop(self): self.ev.append("stop")
        def on_order_submitted(self, order): self.ev.append(("submitted", order.kind, order.side))
        def on_bar(self, bar):
            if self.index == 0: self.buy(1.0)
    s = S(); SingleSymbolEngine(_bars(), s).run()
    assert s.ev[0] == "start" and s.ev[-1] == "stop"
    assert ("submitted", "market", 1) in s.ev


def test_on_liquidation_fires():
    # Engine uses next-open fill semantics: the buy submitted in on_bar[0] fills at bar[1].open.
    # To trigger liquidation we need a heavily-leveraged position that collapses at bar[2].
    # buy(900) fills at bar[1].open=100 -> cash = 10000 - 90000 = -80000, pos = 900.
    # At bar[2]: adverse (low) = 1.  eq_ex = -80000 + 900 = -79100 << maint_margin*notional = 450.
    class S(Strategy):
        def __init__(self): self.liq = 0; self.filled = 0
        def on_liquidation(self, fill): self.liq += 1
        def on_order_filled(self, fill): self.filled += 1
        def on_bar(self, bar):
            if self.index == 0: self.buy(900.0)
    bars = [Bar(ts=0, open=100, high=100, low=100, close=100),
            Bar(ts=60, open=100, high=100, low=100, close=100),
            Bar(ts=120, open=1, high=1, low=1, close=1)]
    s = S(); SingleSymbolEngine(bars, s, leverage=10.0, maint_margin=0.5).run()
    assert s.liq == 1 and s.filled >= 1   # forced close fires on_liquidation AND on_order_filled
