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
