"""Integration: scripted venue funding/liquidation frames -> real decoder/mapper -> LiveOmsHub.

Closes the seam between Tasks 1-3 (per-venue mappers) and the hub fold (LiveOmsHub._on_event).
Each test drives the REAL mapper output through the hub and asserts Account state, verifying:
  - Funding frames fold into account.balance with the correct sign (received-positive).
  - Liquidation frames flatten the position, put realized PnL in account.realized_pnl (NOT balance),
    and deduct only the fee from account.balance.
  - The mapper emits NO FillEvent for a liquidation frame (double-fold guard).
  - apply_liquidation is idempotent: a replayed partial autoclose does not double-charge.
"""
from __future__ import annotations

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import FillEvent, OrderRequest
from vike_trader_app.exec.live_oms import LiveOmsHub
from vike_trader_app.exec.order import ManagedOrder, OrderStatus
from vike_trader_app.exec.risk import RiskGate, RiskLimits

from vike_trader_app.exec.bybit.perp_mapper import map_bybit_perp
from vike_trader_app.exec.bybit.funding import decode_bybit_funding_settlements
from vike_trader_app.exec.okx.perp_mapper import map_okx_perp
from vike_trader_app.exec.okx.funding import decode_okx_funding_bills
from vike_trader_app.exec.binance.perp_mapper import map_binance_perp


class _SpyClient:
    def submit(self, request): pass
    def detach(self): pass


def _hub(venue, symbol):
    return LiveOmsHub(bus=EventBus(), account=Account(venue=venue), gate=RiskGate(RiskLimits()),
                      client=_SpyClient(), venue=venue, symbol=symbol)


# ---------------------------------------------------------------------------
# 4.0  Funding-through-the-mapper per venue
# ---------------------------------------------------------------------------

def test_bybit_funding_transaction_log_folds_balance():
    # Bybit funding now comes from /v5/account/transaction-log (received-positive 'funding'), NOT the
    # execution-topic Funding row (whose execFee is the negated cashflow).
    hub = _hub("bybit", "BTCUSDT")
    rows = [{"id": "1", "type": "SETTLEMENT", "symbol": "BTCUSDT", "funding": "-0.77",
             "feeRate": "0.0001", "transactionTime": "1"}]
    for ev in decode_bybit_funding_settlements(rows, venue="bybit", symbol="BTCUSDT"):
        hub.bus.publish(ev)
    assert hub.account.balance == -0.77            # paid funding (negative passed through, no flip)


def test_okx_funding_bill_folds_balance():
    hub = _hub("okx", "BTC-USDT-SWAP")
    # A funding bill carries pnl (documented) == balChg (fee==0); the decoder reads pnl.
    bills = [{"billId": "b1", "instId": "BTC-USDT-SWAP", "type": "8",
              "subType": "174", "pnl": "1.30", "balChg": "1.30", "fee": "0", "ts": "1"}]
    for ev in decode_okx_funding_bills(bills, venue="okx", symbol="BTC-USDT-SWAP"):
        hub.bus.publish(ev)
    assert hub.account.balance == 1.30


def test_binance_funding_frame_folds_balance():
    hub = _hub("binance", "BTCUSDT")
    frame = {"e": "ACCOUNT_UPDATE", "T": 1, "a": {"m": "FUNDING_FEE",
             "B": [{"a": "USDT", "bc": "-0.50"}]}}
    for ev in map_binance_perp(frame, venue="binance", symbol="BTCUSDT"):
        hub.bus.publish(ev)
    assert hub.account.balance == -0.50


# ---------------------------------------------------------------------------
# 4.1  Liquidation-through-the-mapper per venue
# ---------------------------------------------------------------------------

def _seed_long(hub, venue, symbol, coid="o1", qty=2.0, px=100.0):
    req = OrderRequest(client_order_id=coid, venue=venue, symbol=symbol,
                       side=+1, qty=qty, order_type="limit", price=px)
    hub.registry[coid] = ManagedOrder(request=req, status=OrderStatus.ACCEPTED)
    hub.bus.publish(FillEvent(trade_id="t0", client_order_id=coid, venue=venue,
                              symbol=symbol, side=+1, last_qty=qty, last_px=px))


def test_bybit_busttrade_flattens_and_liquidates_fsm():
    hub = _hub("bybit", "BTCUSDT")
    _seed_long(hub, "bybit", "BTCUSDT")
    frame = {"topic": "execution", "data": [{
        "execType": "BustTrade", "symbol": "BTCUSDT", "side": "Sell",
        "execQty": "2.0", "execPrice": "60.0", "execFee": "0.5", "execTime": "2"}]}
    for ev in map_bybit_perp(frame, venue="bybit", symbol="BTCUSDT"):
        hub.bus.publish(ev)
    assert hub.account.positions[("bybit", "BTCUSDT", "BOTH")]["size"] == 0.0
    assert hub.account.realized_pnl == -80.0       # (60-100)*2
    assert hub.account.balance == -0.5
    assert hub.registry["o1"].status is OrderStatus.LIQUIDATED


def test_okx_full_liquidation_flattens_and_liquidates_fsm():
    hub = _hub("okx", "BTC-USDT-SWAP")
    _seed_long(hub, "okx", "BTC-USDT-SWAP")
    # ct_val=1.0 here so 2 contracts == 2.0 base, matching the seeded size. tradeId POPULATED (a real
    # liquidation fill carries one) — proves the category branch suppresses the otherwise-emitted fill.
    frame = {"arg": {"channel": "orders", "instType": "SWAP"}, "data": [{
        "category": "full_liquidation", "instId": "BTC-USDT-SWAP", "posSide": "net",
        "fillSz": "2", "fillPx": "60.0", "fillFee": "-0.5", "tradeId": "T9", "fillTime": "2"}]}
    for ev in map_okx_perp(frame, venue="okx", symbol="BTC-USDT-SWAP", ct_val=1.0):
        hub.bus.publish(ev)
    assert hub.account.positions[("okx", "BTC-USDT-SWAP", "BOTH")]["size"] == 0.0
    assert hub.account.realized_pnl == -80.0       # PnL in realized_pnl, NOT balance
    assert hub.account.balance == -0.5             # only the fee hits balance
    assert hub.registry["o1"].status is OrderStatus.LIQUIDATED


def test_binance_autoclose_flattens_and_liquidates_fsm():
    hub = _hub("binance", "BTCUSDT")
    _seed_long(hub, "binance", "BTCUSDT")
    frame = {"e": "ORDER_TRADE_UPDATE", "T": 2, "o": {
        "s": "BTCUSDT", "c": "autoclose-123", "x": "TRADE", "X": "FILLED",
        "S": "SELL", "ps": "BOTH", "l": "2.0", "L": "60.0", "n": "0.5", "t": 9}}
    for ev in map_binance_perp(frame, venue="binance", symbol="BTCUSDT"):
        hub.bus.publish(ev)
    assert hub.account.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 0.0
    assert hub.account.realized_pnl == -80.0       # PnL in realized_pnl, NOT balance
    assert hub.account.balance == -0.5             # only the fee hits balance
    assert hub.registry["o1"].status is OrderStatus.LIQUIDATED


def test_binance_multi_partial_autoclose_charges_per_distinct_id():
    # A forced close can stream as MULTIPLE partial autoclose- TRADE frames. With the 5g-1 fix each
    # partial closes ITS OWN qty (clamped) and charges ITS OWN fee, keyed by a DISTINCT OTU trade id.
    # Two partials of 1.0 each sum to the held 2.0 @ 100 -> flat; realized -80 (-40 + -40); fee 0.5
    # twice -> balance -1.0. A REPLAYED id (the WS reconnect re-pushes the first frame) is dropped by
    # _seen_liq_ids and changes nothing (no double-close, no double-fee). t==0 means no dedup, but a
    # real venue partial always carries a distinct non-zero trade id, so we use 9 then 10.
    hub = _hub("binance", "BTCUSDT")
    _seed_long(hub, "binance", "BTCUSDT")          # long 2.0 @ 100
    for t_id in (9, 10):                            # two DISTINCT partials of the same forced close
        frame = {"e": "ORDER_TRADE_UPDATE", "T": 3, "o": {
            "s": "BTCUSDT", "c": "autoclose-9", "x": "TRADE", "X": "PARTIALLY_FILLED",
            "S": "SELL", "ps": "BOTH", "l": "1.0", "L": "60.0", "n": "0.5", "t": t_id}}
        for ev in map_binance_perp(frame, venue="binance", symbol="BTCUSDT"):
            hub.bus.publish(ev)
    assert hub.account.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 0.0
    assert hub.account.realized_pnl == -80.0       # -40 per distinct partial, summed
    assert hub.account.balance == -1.0             # 0.5 per distinct id, twice
    assert hub.registry["o1"].status is OrderStatus.LIQUIDATED

    # REPLAY the first partial (id 9) — reconnect re-push must NOT double-close or double-charge.
    replay = {"e": "ORDER_TRADE_UPDATE", "T": 3, "o": {
        "s": "BTCUSDT", "c": "autoclose-9", "x": "TRADE", "X": "PARTIALLY_FILLED",
        "S": "SELL", "ps": "BOTH", "l": "1.0", "L": "60.0", "n": "0.5", "t": 9}}
    for ev in map_binance_perp(replay, venue="binance", symbol="BTCUSDT"):
        hub.bus.publish(ev)
    assert hub.account.realized_pnl == -80.0       # unchanged — replay dropped by _seen_liq_ids
    assert hub.account.balance == -1.0             # fee NOT re-charged


def test_okx_partial_liquidation_leaves_residual():
    # An OKX partial_liquidation closing only PART (fillSz=1 of a 2.0 pos) must leave size==1.0, not
    # flatten the whole book (the 5e over-close bug). ct_val=1.0 so 1 contract == 1.0 base.
    hub = _hub("okx", "BTC-USDT-SWAP")
    _seed_long(hub, "okx", "BTC-USDT-SWAP")        # long 2.0 @ 100
    frame = {"arg": {"channel": "orders", "instType": "SWAP"}, "data": [{
        "category": "partial_liquidation", "instId": "BTC-USDT-SWAP", "posSide": "net",
        "fillSz": "1", "fillPx": "60.0", "fillFee": "-0.5", "tradeId": "PL1", "fillTime": "2"}]}
    for ev in map_okx_perp(frame, venue="okx", symbol="BTC-USDT-SWAP", ct_val=1.0):
        hub.bus.publish(ev)
    assert hub.account.positions[("okx", "BTC-USDT-SWAP", "BOTH")]["size"] == 1.0  # residual, not 0
    assert hub.account.realized_pnl == -40.0       # (60-100)*1
    assert hub.account.balance == -0.5             # one fee


def test_liquidation_replay_same_id_dropped_at_hub():
    # The hub-level _seen_liq_ids dedup: replaying the SAME tradeId (reconnect) must not re-apply.
    hub = _hub("okx", "BTC-USDT-SWAP")
    _seed_long(hub, "okx", "BTC-USDT-SWAP")        # long 2.0 @ 100
    frame = {"arg": {"channel": "orders", "instType": "SWAP"}, "data": [{
        "category": "partial_liquidation", "instId": "BTC-USDT-SWAP", "posSide": "net",
        "fillSz": "1", "fillPx": "60.0", "fillFee": "-0.5", "tradeId": "DUP", "fillTime": "2"}]}
    for _ in range(2):                             # publish the SAME frame twice
        for ev in map_okx_perp(frame, venue="okx", symbol="BTC-USDT-SWAP", ct_val=1.0):
            hub.bus.publish(ev)
    assert hub.account.positions[("okx", "BTC-USDT-SWAP", "BOTH")]["size"] == 1.0  # closed once
    assert hub.account.realized_pnl == -40.0       # not -80
    assert hub.account.balance == -0.5             # fee once


# ---------------------------------------------------------------------------
# 4.2  Double-fold negative guard — no FillEvent in any liquidation mapper output
# ---------------------------------------------------------------------------

def test_no_fillevent_for_any_liquidation_frame():
    bybit = map_bybit_perp({"topic": "execution", "data": [{
        "execType": "BustTrade", "symbol": "BTCUSDT", "execQty": "1", "execPrice": "1"}]},
        venue="bybit", symbol="BTCUSDT")
    okx = map_okx_perp({"arg": {"channel": "orders"}, "data": [{
        "category": "adl", "instId": "BTC-USDT-SWAP", "fillSz": "1", "fillPx": "1", "tradeId": "T5"}]},
        venue="okx", symbol="BTC-USDT-SWAP", ct_val=1.0)
    binance = map_binance_perp({"e": "ORDER_TRADE_UPDATE", "T": 1, "o": {
        "s": "BTCUSDT", "c": "autoclose-1", "x": "TRADE", "l": "1", "L": "1"}},
        venue="binance", symbol="BTCUSDT")
    for evs in (bybit, okx, binance):
        assert not any(isinstance(e, FillEvent) for e in evs)
        assert all(type(e).__name__ == "PositionLiquidated" for e in evs)
