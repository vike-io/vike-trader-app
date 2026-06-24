"""FillEvent.position_side defaults to BOTH (spot byte-identical); accepts LONG/SHORT for hedge perps."""

from vike_trader_app.exec.events import FillEvent


def test_fill_event_position_side_defaults_to_both():
    f = FillEvent(trade_id="t", client_order_id="c", venue="v", symbol="X",
                  side=1, last_qty=1.0, last_px=100.0)
    assert f.position_side == "BOTH"


def test_fill_event_accepts_hedge_side():
    f = FillEvent(trade_id="t", client_order_id="c", venue="v", symbol="X",
                  side=1, last_qty=1.0, last_px=100.0, position_side="LONG")
    assert f.position_side == "LONG"
