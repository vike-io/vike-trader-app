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


def test_position_liquidated_carries_trade_id():
    from vike_trader_app.exec.events import PositionLiquidated

    ev = PositionLiquidated(venue="okx", symbol="BTC-USDT-SWAP", position_side="BOTH",
                            qty=1.0, liq_price=60.0, fee=0.5, ts=2, trade_id="T9")
    assert ev.trade_id == "T9"


def test_position_liquidated_trade_id_defaults_empty():
    from vike_trader_app.exec.events import PositionLiquidated

    ev = PositionLiquidated(venue="okx", symbol="BTC-USDT-SWAP", position_side="BOTH",
                            qty=1.0, liq_price=60.0)
    assert ev.trade_id == ""        # additive default keeps every existing construction valid
