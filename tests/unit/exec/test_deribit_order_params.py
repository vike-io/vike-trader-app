"""DeribitExecutionClient.build_order_params: OrderRequest -> private/buy|sell params.

post_only MUST be False (Deribit defaults it True -> a crossing order would be rejected/repriced).
amount is in COIN units; label carries the client_order_id; limit carries price, market does not.
"""
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.deribit.client import DeribitExecutionClient
from vike_trader_app.exec.events import OrderRequest

_FILTERS = {"tick_size": 0.0001, "step_size": 0.1, "min_qty": 0.1,
            "max_qty": 1000.0, "min_notional": 0.0}


def _client(transport=lambda method, params: {}):
    return DeribitExecutionClient(EventBus(), transport=transport,
                                  symbol="BTC-27JUN25-60000-C", filters=_FILTERS, currency="BTC")


def _req(side=+1, qty=1.0, order_type="limit", price=0.05, coid="sess-0", reduce_only=False):
    return OrderRequest(client_order_id=coid, venue="deribit", symbol="BTC-27JUN25-60000-C",
                        side=side, qty=qty, order_type=order_type, price=price,
                        reduce_only=reduce_only)


def test_limit_buy_params():
    p = _client().build_order_params(_req(side=+1, qty=1.3, order_type="limit", price=0.0523))
    assert p["instrument_name"] == "BTC-27JUN25-60000-C"
    assert p["amount"] == 1.3                 # qty quantized to step_size=0.1
    assert p["type"] == "limit"
    assert p["price"] == 0.0523               # quantized to tick_size=0.0001
    assert p["label"] == "sess-0"
    assert p["post_only"] is False            # CRITICAL: never default-True for a crossing order
    assert "reduce_only" not in p             # omitted when False


def test_market_sell_params_have_no_price():
    p = _client().build_order_params(_req(side=-1, qty=0.5, order_type="market", price=None))
    assert p["type"] == "market"
    assert "price" not in p
    assert p["post_only"] is False


def test_qty_quantized_down_to_step():
    # 1.37 with step 0.1 quantizes DOWN to 1.3 (format_to_step ROUND_DOWN)
    p = _client().build_order_params(_req(qty=1.37, order_type="limit", price=0.05))
    assert p["amount"] == 1.3


def test_reduce_only_passes_through_when_true():
    p = _client().build_order_params(_req(order_type="market", price=None, reduce_only=True))
    assert p["reduce_only"] is True
