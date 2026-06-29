"""Task B1: OrderRequest move to core + additive fields.

Tests:
1. Additive backtest fields default correctly.
2. exec.events.OrderRequest IS core.order_intent.OrderRequest (same class object via re-export).
3. Required-field construction + side validation still work.
"""
from __future__ import annotations

import pytest


def _make_req(**overrides):
    """Build a minimal valid OrderRequest."""
    from vike_trader_app.core.order_intent import OrderRequest
    defaults = dict(
        client_order_id="coid-1",
        venue="binance",
        symbol="BTCUSDT",
        side=1,
        qty=0.01,
        order_type="market",
    )
    defaults.update(overrides)
    return OrderRequest(**defaults)


class TestAdditiveFields:
    def test_weight_defaults_zero(self):
        req = _make_req()
        assert req.weight == 0.0

    def test_stop_defaults_none(self):
        req = _make_req()
        assert req.stop is None

    def test_trail_defaults_none(self):
        req = _make_req()
        assert req.trail is None

    def test_extreme_defaults_none(self):
        req = _make_req()
        assert req.extreme is None

    def test_on_close_defaults_false(self):
        req = _make_req()
        assert req.on_close is False

    def test_additive_fields_settable(self):
        """Ensure the fields are keyword-settable without breaking positional construction."""
        from vike_trader_app.core.order_intent import OrderRequest
        req = OrderRequest(
            client_order_id="coid-2",
            venue="bybit",
            symbol="ETHUSDT",
            side=-1,
            qty=1.0,
            order_type="limit",
            price=3000.0,
            weight=0.5,
            stop=2900.0,
            trail=50.0,
            extreme=3100.0,
            on_close=True,
        )
        assert req.weight == 0.5
        assert req.stop == 2900.0
        assert req.trail == 50.0
        assert req.extreme == 3100.0
        assert req.on_close is True


class TestReExport:
    def test_same_class_object(self):
        """exec.events.OrderRequest must be the SAME class as core.order_intent.OrderRequest."""
        from vike_trader_app.exec.events import OrderRequest as ExecOR
        from vike_trader_app.core.order_intent import OrderRequest as CoreOR
        assert ExecOR is CoreOR

    def test_adapter_reexport_identity(self):
        """core.orders.order_request_to_resting IS core.order_intent.order_request_to_resting."""
        import vike_trader_app.core.orders as orders_mod
        import vike_trader_app.core.order_intent as intent_mod
        assert orders_mod.order_request_to_resting is intent_mod.order_request_to_resting

    def test_import_from_exec_events_still_works(self):
        """All existing exec importers must keep working."""
        from vike_trader_app.exec.events import OrderRequest
        req = OrderRequest(
            client_order_id="coid-3",
            venue="okx",
            symbol="BTC-USDT",
            side=1,
            qty=0.001,
            order_type="market",
        )
        assert req.symbol == "BTC-USDT"


class TestRequiredFields:
    def test_positional_construction(self):
        """Ensure required fields still work positionally (first 6)."""
        from vike_trader_app.core.order_intent import OrderRequest
        req = OrderRequest("coid-4", "binance", "SOLUSDT", -1, 10.0, "limit", price=150.0)
        assert req.client_order_id == "coid-4"
        assert req.venue == "binance"
        assert req.symbol == "SOLUSDT"
        assert req.side == -1
        assert req.qty == 10.0
        assert req.order_type == "limit"
        assert req.price == 150.0

    def test_side_buy(self):
        req = _make_req(side=1)
        assert req.side == 1

    def test_side_sell(self):
        req = _make_req(side=-1)
        assert req.side == -1

    def test_frozen(self):
        """OrderRequest is frozen — mutation must raise."""
        req = _make_req()
        with pytest.raises(Exception):
            req.qty = 999.0  # type: ignore[misc]

    def test_contingency_slots_present(self):
        """Reserved contingency fields from original must still be present."""
        req = _make_req()
        assert req.parent_order_id is None
        assert req.linked_order_ids == ()
        assert req.order_list_id is None
        assert req.contingency_type is None

    def test_trigger_price_and_reduce_only(self):
        req = _make_req(order_type="stop", trigger_price=50000.0, reduce_only=True)
        assert req.trigger_price == 50000.0
        assert req.reduce_only is True


# ---------------------------------------------------------------------------
# Task B2: adapter + builder round-trip (6 kinds)
# ---------------------------------------------------------------------------

class TestRoundTrip:
    """backtest_order_request(...) → order_request_to_resting(...) must equal the
    core.Order the verb builds today — byte-identical across all 6 kinds."""

    def _rt(self, **kwargs):
        from vike_trader_app.core.order_intent import backtest_order_request, order_request_to_resting
        req = backtest_order_request(**kwargs)
        return order_request_to_resting(req)

    def _order(self, *args, **kwargs):
        from vike_trader_app.core.orders import Order
        return Order(*args, **kwargs)

    # --- kind: market (no stop) ---
    def test_market_no_stop(self):
        o = self._rt(side=1, qty=10.0, order_type="market", weight=0.3)
        expected = self._order("market", 1, 10.0, weight=0.3, stop=None)
        assert o.kind == expected.kind
        assert o.side == expected.side
        assert o.size == expected.size
        assert o.price == expected.price
        assert o.trail == expected.trail
        assert o.extreme == expected.extreme
        assert o.weight == expected.weight
        assert o.stop == expected.stop

    # --- kind: market (with stop) ---
    def test_market_with_stop(self):
        o = self._rt(side=1, qty=5.0, order_type="market", weight=0.5, stop=95.0)
        expected = self._order("market", 1, 5.0, weight=0.5, stop=95.0)
        assert o.kind == expected.kind
        assert o.side == expected.side
        assert o.size == expected.size
        assert o.price == expected.price
        assert o.trail == expected.trail
        assert o.extreme == expected.extreme
        assert o.weight == expected.weight
        assert o.stop == expected.stop

    # --- kind: limit (no stop) ---
    def test_limit_no_stop(self):
        o = self._rt(side=1, qty=2.0, order_type="limit", price=100.0, weight=0.1)
        expected = self._order("limit", 1, 2.0, price=100.0, weight=0.1, stop=None)
        assert o.kind == expected.kind
        assert o.side == expected.side
        assert o.size == expected.size
        assert o.price == expected.price
        assert o.trail == expected.trail
        assert o.extreme == expected.extreme
        assert o.weight == expected.weight
        assert o.stop == expected.stop

    # --- kind: limit (with stop) ---
    def test_limit_with_stop(self):
        o = self._rt(side=-1, qty=3.0, order_type="limit", price=200.0, weight=0.2, stop=210.0)
        expected = self._order("limit", -1, 3.0, price=200.0, weight=0.2, stop=210.0)
        assert o.kind == expected.kind
        assert o.side == expected.side
        assert o.size == expected.size
        assert o.price == expected.price
        assert o.trail == expected.trail
        assert o.extreme == expected.extreme
        assert o.weight == expected.weight
        assert o.stop == expected.stop

    # --- kind: stop ---
    def test_stop(self):
        o = self._rt(side=1, qty=4.0, order_type="stop", trigger_price=105.0, weight=0.4)
        expected = self._order("stop", 1, 4.0, price=105.0, weight=0.4)
        assert o.kind == expected.kind
        assert o.side == expected.side
        assert o.size == expected.size
        assert o.price == expected.price
        assert o.trail == expected.trail
        assert o.extreme == expected.extreme
        assert o.weight == expected.weight
        assert o.stop == expected.stop

    # --- kind: trailing (extreme seeded) ---
    def test_trailing_with_extreme(self):
        o = self._rt(side=-1, qty=1.0, order_type="market", trail=5.0, extreme=120.0, weight=0.6)
        expected = self._order("trailing", -1, 1.0, trail=5.0, extreme=120.0, weight=0.6)
        assert o.kind == expected.kind
        assert o.side == expected.side
        assert o.size == expected.size
        assert o.price == expected.price
        assert o.trail == expected.trail
        assert o.extreme == expected.extreme
        assert o.weight == expected.weight
        assert o.stop == expected.stop

    # --- kind: market_close ---
    def test_market_close(self):
        o = self._rt(side=-1, qty=7.0, order_type="market", on_close=True, weight=0.7)
        expected = self._order("market_close", -1, 7.0, weight=0.7)
        assert o.kind == expected.kind
        assert o.side == expected.side
        assert o.size == expected.size
        assert o.price == expected.price
        assert o.trail == expected.trail
        assert o.extreme == expected.extreme
        assert o.weight == expected.weight
        assert o.stop == expected.stop

    # --- kind: limit_close ---
    def test_limit_close(self):
        o = self._rt(side=-1, qty=8.0, order_type="limit", price=150.0, on_close=True, weight=0.8)
        expected = self._order("limit_close", -1, 8.0, price=150.0, weight=0.8)
        assert o.kind == expected.kind
        assert o.side == expected.side
        assert o.size == expected.size
        assert o.price == expected.price
        assert o.trail == expected.trail
        assert o.extreme == expected.extreme
        assert o.weight == expected.weight
        assert o.stop == expected.stop

    # --- mutability: returned Order must be mutable ---
    def test_returned_order_is_mutable(self):
        o = self._rt(side=-1, qty=1.0, order_type="market", trail=3.0, extreme=50.0, weight=0.1)
        # engines ratchet extreme and cap size in place
        o.extreme = 55.0
        assert o.extreme == 55.0
        o.size = 0.5
        assert o.size == 0.5
