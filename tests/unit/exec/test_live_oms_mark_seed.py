"""submit_ticket must seed RiskContext.mark_price from the Account marks for a perp MARKET order
(price=None), so the min_notional/notional gate rules evaluate against the real mark, not 0.0."""
from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.events import OrderDenied, OrderRequest
from vike_trader_app.exec.live_oms import LiveOmsHub
from vike_trader_app.exec.risk import RiskGate, RiskLimits


class _Bus:
    def __init__(self): self.published = []
    def subscribe(self, fn): pass
    def unsubscribe(self, fn): pass
    def publish(self, ev): self.published.append(ev)


class _Client:
    def __init__(self): self.submitted = []
    def submit(self, req): self.submitted.append(req)


def _market(qty):
    return OrderRequest(client_order_id="c1", venue="binance", symbol="BTCUSDT",
                        side=1, qty=qty, order_type="market", price=None)


def test_market_order_passes_min_notional_via_seeded_mark():
    bus, client, acc = _Bus(), _Client(), Account()
    acc.set_mark("binance", "BTCUSDT", 30000.0)          # mark from the perp mapper feed
    gate = RiskGate(RiskLimits(min_notional=100.0))
    hub = LiveOmsHub(bus=bus, account=acc, gate=gate, client=client,
                     venue="binance", symbol="BTCUSDT")
    hub.submit_ticket(_market(0.01))                      # 0.01 * 30000 = 300 >= 100
    assert client.submitted, "order must reach the client, not be min-notional-vetoed"
    assert not any(isinstance(e, OrderDenied) for e in bus.published)


def test_market_order_without_mark_is_denied_not_crash():
    bus, client, acc = _Bus(), _Client(), Account()    # no mark recorded
    gate = RiskGate(RiskLimits(min_notional=100.0))
    hub = LiveOmsHub(bus=bus, account=acc, gate=gate, client=client,
                     venue="binance", symbol="BTCUSDT")
    hub.submit_ticket(_market(0.01))                      # mark 0 -> notional 0 -> denied (graceful)
    assert any(isinstance(e, OrderDenied) for e in bus.published)
    assert not client.submitted


def _limit(qty, price):
    return OrderRequest(client_order_id="c2", venue="binance", symbol="BTCUSDT",
                        side=1, qty=qty, order_type="limit", price=price)


def test_limit_order_uses_own_price_not_stale_mark():
    """A LIMIT order carries a price; the mark seed must NOT replace it with account.marks.
    The gate must evaluate the order at the limit price, not the (possibly-stale) mark,
    so the exposure/min-notional outcome is unchanged regardless of what the mark says."""
    bus, client, acc = _Bus(), _Client(), Account()
    # Seed a DIFFERENT mark — if the implementation wrongly uses the mark for limit orders,
    # the notional will be wrong (0.01 * 50000 = 500 instead of 0.01 * 30000 = 300).
    acc.set_mark("binance", "BTCUSDT", 50000.0)
    gate = RiskGate(RiskLimits(min_notional=100.0, max_notional_per_order=400.0))
    hub = LiveOmsHub(bus=bus, account=acc, gate=gate, client=client,
                     venue="binance", symbol="BTCUSDT")
    # LIMIT at 30000: notional = 0.01 * 30000 = 300, which is UNDER the 400 max cap -> must PASS
    # If the mark (50000) were used instead: 0.01 * 50000 = 500 > 400 -> would WRONGLY reject
    hub.submit_ticket(_limit(0.01, 30000.0))
    assert client.submitted, "limit order at its own price must pass (300 < 400); not rejected by stale mark"
    assert not any(isinstance(e, OrderDenied) for e in bus.published)
