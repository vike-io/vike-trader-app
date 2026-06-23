"""The base flow calls each venue hook; a fake subclass proves the wiring without any venue specifics."""

from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.crypto_client import CryptoExecutionClient, ReconcileSnapshot, VenueApiError
from vike_trader_app.exec.events import OrderAccepted, OrderRequest, OrderSubmitted


class _FakeClient(CryptoExecutionClient):
    VENUE = "fake"
    PATH_ORDER_CREATE = "/create"
    PATH_ORDER_CANCEL = "/cancel"
    PATH_OPEN_ORDERS = "/open"
    PATH_ACCOUNT = "/acct"
    PATH_TICKER = "/tick"

    def build_order_params(self, r):
        return {"coid": r.client_order_id}

    def build_cancel_params(self, coid):
        return {"coid": coid}

    def build_account_params(self):
        return {"a": 1}

    def build_open_orders_params(self):
        return {"o": 1}

    def build_ticker_params(self):
        return {"t": 1}

    def parse_venue_order_id(self, resp):
        return str(resp.get("oid", ""))

    def iter_balances(self, acct):
        for b in acct.get("bals", []):
            yield {"asset": b["asset"], "free": b["free"]}

    def iter_open_orders(self, resp):
        for o in resp.get("orders", []):
            yield {"side": o["side"], "orig_qty": o["orig"], "executed_qty": o["exec"],
                   "coid": o["coid"], "order_type": "limit", "price": o["price"],
                   "venue_order_id": o["oid"]}

    def parse_mark_px(self, t):
        return float(t["px"])

    def is_order_not_found(self, code):
        return code == 999

    def unwrap(self, resp):
        if resp.get("err"):
            raise VenueApiError(resp["err"], resp.get("msg", ""))
        return resp


_FILTERS = {"tick_size": 0.01, "step_size": 0.001}


def _req(coid="c-0"):
    return OrderRequest(client_order_id=coid, venue="fake", symbol="BTCUSDT",
                        side=+1, qty=0.3, order_type="limit", price=65000.0)


def test_submit_publishes_submitted_then_accepted_and_uses_hooks():
    seen = []

    def _transport(base, path, method, params, signer, **kw):
        assert path == "/create"
        assert params == {"coid": "c-0"}
        return {"oid": 42}

    bus = EventBus()
    bus.subscribe(seen.append)
    client = _FakeClient(bus, signer=object(), rest_base_url="https://x", symbol="BTCUSDT",
                         filters=_FILTERS, transport=_transport)
    client.submit(_req())
    assert [type(e).__name__ for e in seen] == ["OrderSubmitted", "OrderAccepted"]
    acc = [e for e in seen if isinstance(e, OrderAccepted)][0]
    assert acc.venue_order_id == "42"


def test_submit_rejects_when_unwrap_raises():
    def _transport(base, path, method, params, signer, **kw):
        return {"err": 123, "msg": "nope"}

    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    client = _FakeClient(bus, signer=object(), rest_base_url="https://x", symbol="BTCUSDT",
                         filters=_FILTERS, transport=_transport)
    client.submit(_req())
    assert [type(e).__name__ for e in seen] == ["OrderSubmitted", "OrderRejected"]


def test_cancel_swallows_not_found_and_reraises_others():
    def _nf(*a, **kw):
        return {"err": 999, "msg": "gone"}

    bus = EventBus()
    _FakeClient(bus, signer=object(), rest_base_url="https://x", symbol="BTCUSDT",
                filters=_FILTERS, transport=_nf).cancel("c-0")  # must not raise

    def _other(*a, **kw):
        return {"err": 7, "msg": "boom"}

    import pytest
    with pytest.raises(VenueApiError) as ei:
        _FakeClient(bus, signer=object(), rest_base_url="https://x", symbol="BTCUSDT",
                    filters=_FILTERS, transport=_other).cancel("c-0")
    assert ei.value.code == 7


def test_connect_assembles_snapshot_with_locked_sell_addback():
    calls = {}

    def _transport(base, path, method, params, signer, **kw):
        calls[path] = params
        if path == "/acct":
            return {"bals": [{"asset": "BTC", "free": "0.5"}]}
        if path == "/open":
            return {"orders": [{"side": -1, "orig": 0.2, "exec": 0.05, "coid": "p-1",
                                "price": 70000.0, "oid": "9"}]}
        raise AssertionError(path)

    def _public(base, path, params):
        assert path == "/tick"
        return {"px": "65000.0"}

    bus = EventBus()
    client = _FakeClient(bus, signer=object(), rest_base_url="https://x", symbol="BTCUSDT",
                         filters=_FILTERS, base_asset="BTC", transport=_transport,
                         public_transport=_public)
    snap = client.connect()
    assert isinstance(snap, ReconcileSnapshot)
    # seeded = free(0.5) + locked_sell(0.2 - 0.05 = 0.15) = 0.65
    assert snap.positions == (("BTCUSDT", 0.65),)
    assert snap.position_avg_px == (("BTCUSDT", 65000.0),)
    assert len(snap.open_orders) == 1
    assert snap.open_orders[0].venue_order_id == "9"
    assert calls["/acct"] == {"a": 1}
    assert calls["/open"] == {"o": 1}
