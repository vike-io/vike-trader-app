"""submit() is ACK-only: OrderSubmitted->OrderAccepted on 2xx, OrderRejected on error, no FillEvent."""

import pytest

from vike_trader_app.exec.binance.client import BinanceSpotExecutionClient
from vike_trader_app.exec.binance.transport import BinanceApiError
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import (
    FillEvent,
    OrderAccepted,
    OrderRejected,
    OrderRequest,
    OrderSubmitted,
)

_FILTERS = {"tick_size": 0.01, "step_size": 0.001, "min_qty": 0.001,
            "max_qty": 9000.0, "min_notional": 5.0}


def _seen(bus):
    out = []
    bus.subscribe(out.append)
    return out


def _req(coid="sess-0"):
    return OrderRequest(client_order_id=coid, venue="binance", symbol="BTCUSDT",
                        side=+1, qty=0.30000000000000004, order_type="limit", price=65000.0)


def test_submit_acks_with_accepted_and_no_fill():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        captured.update(params=params, method=method, path=path)
        return {"orderId": 12345, "status": "NEW", "fills": [{"price": "65000", "qty": "0.3"}]}

    bus = EventBus()
    seen = _seen(bus)
    client = BinanceSpotExecutionClient(bus, signer=object(), rest_base_url="https://x",
                                        symbol="BTCUSDT", filters=_FILTERS, transport=_transport)
    client.submit(_req())
    kinds = [type(e).__name__ for e in seen]
    assert kinds == ["OrderSubmitted", "OrderAccepted"]
    assert not any(isinstance(e, FillEvent) for e in seen)   # POST fills[] is IGNORED
    acc = [e for e in seen if isinstance(e, OrderAccepted)][0]
    assert acc.venue_order_id == "12345"
    # qty/price went out as decimal strings (no IEEE artifact), with the real newClientOrderId
    assert captured["params"]["quantity"] == "0.300"
    assert captured["params"]["price"] == "65000.00"
    assert captured["params"]["newClientOrderId"] == "sess-0"
    assert captured["method"] == "POST"


def test_submit_rejects_on_api_error():
    def _transport(*a, **kw):
        raise BinanceApiError(-2010, "Filter failure: MIN_NOTIONAL")

    bus = EventBus()
    seen = _seen(bus)
    client = BinanceSpotExecutionClient(bus, signer=object(), rest_base_url="https://x",
                                        symbol="BTCUSDT", filters=_FILTERS, transport=_transport)
    client.submit(_req())
    assert [type(e).__name__ for e in seen] == ["OrderSubmitted", "OrderRejected"]
    rej = [e for e in seen if isinstance(e, OrderRejected)][0]
    assert "MIN_NOTIONAL" in rej.reason


def test_cancel_issues_delete():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        captured.update(method=method, params=params)
        return {"status": "CANCELED"}

    bus = EventBus()
    client = BinanceSpotExecutionClient(bus, signer=object(), rest_base_url="https://x",
                                        symbol="BTCUSDT", filters=_FILTERS, transport=_transport)
    client.cancel("sess-0")
    assert captured["method"] == "DELETE"
    assert captured["params"]["origClientOrderId"] == "sess-0"
