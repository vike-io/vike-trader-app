"""5g-3 end-to-end OBSERVE proof: a hedge reconcile snapshot seeds two Account legs, and a Bybit
hedge fill (positionIdx) lands on the matching leg via the reused 5g-2 apply_fill. Net path stays
byte-equivalent. All offline; no network."""
from __future__ import annotations

import pytest

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.bybit.perp_client import BybitPerpExecutionClient
from vike_trader_app.exec.bybit.perp_mapper import map_bybit_perp
from vike_trader_app.exec.live_oms import LiveOmsHub
from vike_trader_app.exec.risk import RiskGate, RiskLimits

_F = {"tick_size": 0.1, "step_size": 0.001, "min_qty": 0.001, "max_qty": 9e3, "min_notional": 5.0}


class _SpyClient:
    def submit(self, request): pass
    def detach(self): pass


def _hub():
    return LiveOmsHub(bus=EventBus(), account=Account(venue="bybit"), gate=RiskGate(RiskLimits()),
                      client=_SpyClient(), venue="bybit", symbol="BTCUSDT")


def test_hedge_reconcile_then_fill_lands_on_right_leg():
    # 1) Dual-row hedge reconcile from the Bybit perp client (offline transport).
    def t(base, path, method, params, signer, **k):
        return {"retCode": 0, "result": {"list": [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.10", "avgPrice": "65000",
             "markPrice": "65100", "positionIdx": 1},
            {"symbol": "BTCUSDT", "side": "Sell", "size": "0.04", "avgPrice": "64000",
             "markPrice": "65100", "positionIdx": 2},
        ]}}
    client = BybitPerpExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                                      symbol="BTCUSDT", filters=_F, base_asset="BTC",
                                      transport=t, public_transport=lambda *a, **k: {})
    snap = client.connect()

    hub = _hub()
    hub.apply_snapshot(snap)
    assert hub.account.positions[("bybit", "BTCUSDT", "LONG")] == {"size": 0.10, "avg_px": 65000.0}
    assert hub.account.positions[("bybit", "BTCUSDT", "SHORT")] == {"size": -0.04, "avg_px": 64000.0}

    # 2) A SHORT-leg fill (positionIdx=2) adds to the SHORT leg only (reused 5g-2 apply_fill).
    frame = {"topic": "execution", "data": [{
        "category": "linear", "symbol": "BTCUSDT", "side": "Sell", "execId": "ex9",
        "execPrice": "64000", "execQty": "0.01", "execFee": "0.0", "execType": "Trade",
        "leavesQty": "0.0", "orderQty": "0.01", "orderLinkId": "c-short",
        "markPrice": "65100", "execTime": "1700000000001", "isMaker": False,
        "positionIdx": 2}]}
    for ev in map_bybit_perp(frame, venue="bybit", symbol="BTCUSDT"):
        hub.bus.publish(ev)

    assert hub.account.positions[("bybit", "BTCUSDT", "SHORT")]["size"] == pytest.approx(-0.05)  # -0.04 - 0.01
    assert hub.account.positions[("bybit", "BTCUSDT", "LONG")]["size"] == 0.10     # untouched (reconciled)
    assert ("bybit", "BTCUSDT", "BOTH") not in hub.account.positions
    # total_exposure sums abs() of both legs at the shared mark (never nets).
    assert hub.total_exposure() == pytest.approx((0.10 + 0.05) * 65100.0)


def test_net_reconcile_then_fill_is_byte_equivalent():
    """One-way: a positionIdx==0 reconcile -> single BOTH leg; a BOTH fill folds into it."""
    def t(base, path, method, params, signer, **k):
        return {"retCode": 0, "result": {"list": [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.10", "avgPrice": "65000",
             "markPrice": "65100", "positionIdx": 0}]}}
    client = BybitPerpExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                                      symbol="BTCUSDT", filters=_F, base_asset="BTC",
                                      transport=t, public_transport=lambda *a, **k: {})
    hub = _hub()
    hub.apply_snapshot(client.connect())
    assert hub.account.positions[("bybit", "BTCUSDT", "BOTH")] == {"size": 0.10, "avg_px": 65000.0}

    frame = {"topic": "execution", "data": [{
        "category": "linear", "symbol": "BTCUSDT", "side": "Buy", "execId": "ex0",
        "execPrice": "65000", "execQty": "0.02", "execFee": "0.0", "execType": "Trade",
        "leavesQty": "0.0", "orderQty": "0.02", "orderLinkId": "c0",
        "markPrice": "65100", "execTime": "1700000000002", "isMaker": False}]}  # no positionIdx
    for ev in map_bybit_perp(frame, venue="bybit", symbol="BTCUSDT"):
        hub.bus.publish(ev)
    assert hub.account.positions[("bybit", "BTCUSDT", "BOTH")]["size"] == pytest.approx(0.12)   # 0.10 + 0.02
