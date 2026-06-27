from __future__ import annotations
from vike_trader_app.exec.binance.perp_mapper import map_binance_perp
from vike_trader_app.exec.events import (
    FillEvent, FundingEvent, OrderAccepted, OrderCanceled, OrderExpired,
    OrderPartiallyFilled, PositionLiquidated,
)

def _evt(**o_over):
    o = {"s": "BTCUSDT", "c": "c-0", "S": "BUY", "o": "MARKET", "x": "TRADE", "X": "FILLED",
         "i": 99, "l": "0.012", "L": "65000", "n": "0.013", "N": "USDT", "t": 7777,
         "m": False, "R": False, "ps": "BOTH", "ap": "65000", "q": "0.012"}
    o.update(o_over)
    return {"e": "ORDER_TRADE_UPDATE", "E": 1700000000000, "T": 1700000000100, "o": o}

def _acct(m="FUNDING_FEE", balances=((("a", "USDT"), ("bc", "-0.37")),), with_p=False):
    a = {"m": m, "B": [dict(rows) for rows in balances]}
    if with_p:
        a["P"] = [{"s": "BTCUSDT", "pa": "0.5", "ep": "65000"}]
    return {"e": "ACCOUNT_UPDATE", "E": 1700000000000, "T": 1700000000100, "a": a}

def test_trade_fill_dual_publish_identity_and_fields():
    evs = map_binance_perp(_evt(), venue="binance", symbol="BTCUSDT")
    assert [type(e).__name__ for e in evs] == ["FillEvent", "OrderFilled"]
    f = evs[0]
    assert f.trade_id == "7777" and f.client_order_id == "c-0"
    assert f.symbol == "BTCUSDT" and f.side == +1
    assert f.last_qty == 0.012 and f.last_px == 65000.0
    assert f.commission == 0.013 and f.liquidity_side == "taker"
    assert f.position_side == "BOTH" and f.mark_price is None
    assert f.ts == 1700000000100                    # frame['T']
    assert evs[1].fill is evs[0]                     # dual-publish identity

def test_partial_fill_wrap_from_X():
    evs = map_binance_perp(_evt(X="PARTIALLY_FILLED", l="0.004"), venue="binance", symbol="BTCUSDT")
    assert isinstance(evs[1], OrderPartiallyFilled)  # wrap off X, NOT x (every fill is x=TRADE)
    assert evs[0].last_qty == 0.004
    assert evs[1].fill is evs[0]

def test_maker_liquidity():
    evs = map_binance_perp(_evt(m=True), venue="binance", symbol="BTCUSDT")
    assert evs[0].liquidity_side == "maker"

def test_sell_side_and_posside_passthrough():
    evs = map_binance_perp(_evt(S="SELL", ps="SHORT"), venue="binance", symbol="BTCUSDT")
    assert evs[0].side == -1
    assert evs[0].position_side == "SHORT"           # Binance uses the literal strings; no map

def test_new_lifecycle_only():
    evs = map_binance_perp(_evt(x="NEW", X="NEW", t=0), venue="binance", symbol="BTCUSDT")
    assert [type(e).__name__ for e in evs] == ["OrderAccepted"]
    assert isinstance(evs[0], OrderAccepted) and evs[0].venue_order_id == "99"

def test_canceled_lifecycle_only():
    evs = map_binance_perp(_evt(x="CANCELED", X="CANCELED"), venue="binance", symbol="BTCUSDT")
    assert [type(e).__name__ for e in evs] == ["OrderCanceled"]
    assert isinstance(evs[0], OrderCanceled)

def test_expired_lifecycle_only():
    evs = map_binance_perp(_evt(x="EXPIRED", X="EXPIRED"), venue="binance", symbol="BTCUSDT")
    assert isinstance(evs[0], OrderExpired)

def test_calculated_and_amendment_ignored():
    assert map_binance_perp(_evt(x="CALCULATED"), venue="binance", symbol="BTCUSDT") == []
    assert map_binance_perp(_evt(x="AMENDMENT"), venue="binance", symbol="BTCUSDT") == []

def test_symbol_fallback_when_absent():
    frame = _evt()
    del frame["o"]["s"]
    assert map_binance_perp(frame, venue="binance", symbol="ETHUSDT")[0].symbol == "ETHUSDT"

def test_non_order_trade_update_skipped():
    # bare/empty ACCOUNT_UPDATE and non-FUNDING_FEE m values must still return []
    assert map_binance_perp({"e": "ACCOUNT_UPDATE", "a": {}}, venue="binance", symbol="BTCUSDT") == []
    assert map_binance_perp({"e": "listenKeyExpired"}, venue="binance", symbol="BTCUSDT") == []

def test_non_dict_and_missing_o_skipped():
    assert map_binance_perp("pong", venue="binance", symbol="BTCUSDT") == []
    assert map_binance_perp({"e": "ORDER_TRADE_UPDATE"}, venue="binance", symbol="BTCUSDT") == []
    assert map_binance_perp({"e": "ORDER_TRADE_UPDATE", "o": "x"}, venue="binance", symbol="BTCUSDT") == []

# --- FUNDING_FEE tests ---

def test_funding_fee_account_update_emits_funding_event_only():
    evs = map_binance_perp(_acct(), venue="binance", symbol="BTCUSDT")
    assert [type(e).__name__ for e in evs] == ["FundingEvent"]
    ev = evs[0]
    assert isinstance(ev, FundingEvent)
    assert ev.amount == -0.37            # bc, received-positive (negative = paid)
    assert ev.symbol == "BTCUSDT"        # from the hub symbol arg (B row 'a' is the asset, not symbol)
    assert ev.position_side == "BOTH"
    assert ev.mark_price is None         # ACCOUNT_UPDATE carries no mark; funding_rate not here either
    assert ev.funding_rate == 0.0
    assert ev.ts == 1700000000100        # frame['T']
    assert not any(isinstance(e, FillEvent) for e in evs)


def test_funding_fee_positive_bc_is_received():
    # Lock BOTH directions: the live probe only proved sign via sibling income-types (TRANSFER/
    # COMMISSION/REALIZED_PNL), not an actual funding row, so assert the positive case explicitly.
    evs = map_binance_perp(_acct(balances=((("a", "USDT"), ("bc", "0.91")),)),
                           venue="binance", symbol="BTCUSDT")
    assert evs[0].amount == 0.91         # bc>0 = received funding (no flip)


def test_funding_fee_skips_zero_bc_rows():
    evs = map_binance_perp(
        _acct(balances=((("a", "BNB"), ("bc", "0")), (("a", "USDT"), ("bc", "1.20")))),
        venue="binance", symbol="BTCUSDT")
    assert len(evs) == 1
    assert evs[0].amount == 1.20


def test_non_funding_account_update_still_empty():
    assert map_binance_perp(_acct(m="ORDER"), venue="binance", symbol="BTCUSDT") == []
    assert map_binance_perp({"e": "ACCOUNT_UPDATE", "a": {}}, venue="binance", symbol="BTCUSDT") == []
    assert map_binance_perp({"e": "listenKeyExpired"}, venue="binance", symbol="BTCUSDT") == []


# --- LIQUIDATION tests ---

def test_autoclose_trade_emits_liquidation_only():
    evs = map_binance_perp(
        _evt(c="autoclose-1700000000000", l="0.012", L="60000", n="0.5", ps="LONG"),
        venue="binance", symbol="BTCUSDT")
    assert [type(e).__name__ for e in evs] == ["PositionLiquidated"]
    ev = evs[0]
    assert isinstance(ev, PositionLiquidated)
    assert ev.qty == 0.012
    assert ev.liq_price == 60000.0
    assert ev.fee == 0.5
    assert ev.position_side == "LONG"
    assert not any(isinstance(e, FillEvent) for e in evs)


def test_adl_and_settlement_autoclose_prefixes():
    for coid in ("adl_autoclose", "settlement_autoclose-9"):
        evs = map_binance_perp(_evt(c=coid), venue="binance", symbol="BTCUSDT")
        assert [type(e).__name__ for e in evs] == ["PositionLiquidated"]


def test_autoclose_lifecycle_new_frame_does_not_crash_or_liquidate():
    # LIFECYCLE subtlety (verified vs docs): the liquidation order's PLACEMENT update arrives as
    # x=='NEW' with the autoclose- coid; the FLATTEN arrives as a SEPARATE x=='TRADE' update with the
    # SAME coid. The x=='NEW' frame is handled by the existing NEW branch (-> OrderAccepted for the
    # synthetic coid) and MUST NOT emit a PositionLiquidated. Only the x=='TRADE' frame liquidates.
    new_evs = map_binance_perp(_evt(c="autoclose-1", x="NEW", X="NEW"),
                               venue="binance", symbol="BTCUSDT")
    assert not any(type(e).__name__ == "PositionLiquidated" for e in new_evs)  # no liq on placement
    trade_evs = map_binance_perp(_evt(c="autoclose-1", x="TRADE", l="2.0", L="60.0", t="9"),
                                 venue="binance", symbol="BTCUSDT")
    assert [type(e).__name__ for e in trade_evs] == ["PositionLiquidated"]     # the flatten only
    # The autoclose- TRADE row carries a populated tradeId (t='9') yet emits NO FillEvent, so the
    # trade_id never reaches the fill dedup set — the no-double-fold guard holds.
    assert not any(isinstance(e, FillEvent) for e in trade_evs)


def test_normal_trade_not_liquidation():
    # fill-regression guard: a normal coid still dual-publishes a FillEvent.
    evs = map_binance_perp(_evt(c="c-0"), venue="binance", symbol="BTCUSDT")
    assert [type(e).__name__ for e in evs] == ["FillEvent", "OrderFilled"]
    assert evs[1].fill is evs[0]
