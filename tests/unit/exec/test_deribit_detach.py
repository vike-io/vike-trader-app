"""DeribitExecutionClient.detach() — closes the live order transport on teardown.

Covers:
- detach() calls transport.close() when the transport has a close() method
- detach() is a safe no-op when the transport has no close() (the 6a bare-callable fake)
- LiveOmsHub.shutdown() -> detach() end-to-end (the dormant live_oms.py:117 hook now fires)
"""
from __future__ import annotations

from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.deribit.client import DeribitExecutionClient

_FILTERS = {"tick_size": 0.0001, "step_size": 0.1, "min_qty": 0.1,
            "max_qty": 1000.0, "min_notional": 0.0}


def _client(bus, transport):
    return DeribitExecutionClient(bus, transport=transport, symbol="BTC-27JUN25-60000-C",
                                  filters=_FILTERS, currency="BTC")


def test_detach_closes_a_transport_with_close():
    """LiveOmsHub.shutdown's detach hook -> client.detach() -> transport.close()."""
    closed = {"n": 0}

    class _T:
        def __call__(self, method, params):
            return {"id": 1, "result": {"order": {"order_id": "X"}}}
        def close(self):
            closed["n"] += 1

    c = DeribitExecutionClient(EventBus(), transport=_T(), symbol="BTC-27JUN25-60000-C",
                               filters=_FILTERS, currency="BTC")
    c.detach()
    assert closed["n"] == 1


def test_detach_tolerates_a_transport_without_close():
    """The 6a fake transport (a bare callable) has no close() — detach() must be a safe no-op."""
    c = _client(EventBus(), lambda method, params: {"id": 1, "result": {}})
    c.detach()   # must not raise


def test_live_oms_shutdown_invokes_detach():
    """End-to-end: LiveOmsHub.shutdown() reaches DeribitExecutionClient.detach()."""
    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.live_oms import LiveOmsHub
    from vike_trader_app.exec.risk import RiskGate, RiskLimits

    closed = {"n": 0}

    class _T:
        def __call__(self, method, params):
            return {"id": 1, "result": {}}
        def close(self):
            closed["n"] += 1

    bus = EventBus()
    client = DeribitExecutionClient(bus, transport=_T(), symbol="BTC-27JUN25-60000-C",
                                    filters=_FILTERS, currency="BTC")
    hub = LiveOmsHub(bus=bus, account=Account(), gate=RiskGate(RiskLimits()),
                     client=client, venue="deribit", symbol="BTC-27JUN25-60000-C")
    hub.shutdown()
    assert closed["n"] == 1
