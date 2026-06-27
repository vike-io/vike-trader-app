"""connect() returns an empty ReconcileSnapshot in 6a (positions/orders are 6d). The empty-snapshot
contract is pinned so LiveOmsHub.apply_snapshot stays a no-op until 6d adds rows."""
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.crypto_client import ReconcileSnapshot
from vike_trader_app.exec.deribit.client import DeribitExecutionClient

_FILTERS = {"tick_size": 0.0001, "step_size": 0.1, "min_qty": 0.1,
            "max_qty": 1000.0, "min_notional": 0.0}


def test_connect_returns_empty_snapshot():
    c = DeribitExecutionClient(EventBus(), transport=lambda m, p: {"id": 1, "result": {}},
                               symbol="BTC-27JUN25-60000-C", filters=_FILTERS, currency="BTC")
    snap = c.connect()
    assert isinstance(snap, ReconcileSnapshot)
    assert snap.positions == ()
    assert snap.open_orders == ()
