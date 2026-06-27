"""5g-3: apply_snapshot seeds per-leg Account positions from a hedge reconcile snapshot,
with distinct avg_px per leg; a net snapshot stays byte-equivalent (BOTH key)."""
from __future__ import annotations

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.crypto_client import ReconcileSnapshot
from vike_trader_app.exec.live_oms import LiveOmsHub
from vike_trader_app.exec.risk import RiskGate, RiskLimits


class _SpyClient:
    def submit(self, request): pass
    def detach(self): pass


def _hub(venue="binance", symbol="BTCUSDT"):
    return LiveOmsHub(bus=EventBus(), account=Account(), gate=RiskGate(RiskLimits()),
                      client=_SpyClient(), venue=venue, symbol=symbol)


def test_hedge_snapshot_seeds_two_legs_with_distinct_avg():
    hub = _hub()
    snap = ReconcileSnapshot(
        positions=(("BTCUSDT", 0.5), ("BTCUSDT", -0.3)),
        position_avg_px=(("BTCUSDT", 65000.0), ("BTCUSDT", 64000.0)),
        position_mark_px=(("BTCUSDT", 65100.0), ("BTCUSDT", 65100.0)),
        position_sides=(("BTCUSDT", "LONG"), ("BTCUSDT", "SHORT")),
    )
    hub.apply_snapshot(snap)
    assert hub.account.positions[("binance", "BTCUSDT", "LONG")] == {"size": 0.5, "avg_px": 65000.0}
    assert hub.account.positions[("binance", "BTCUSDT", "SHORT")] == {"size": -0.3, "avg_px": 64000.0}
    assert ("binance", "BTCUSDT", "BOTH") not in hub.account.positions
    assert hub.account.marks[("binance", "BTCUSDT")] == 65100.0   # per-symbol mark, shared


def test_net_snapshot_is_byte_equivalent_both_key():
    """No position_sides -> single BOTH leg with symbol-keyed avg_px (pre-5g-3 behavior)."""
    hub = _hub()
    snap = ReconcileSnapshot(
        positions=(("BTCUSDT", 0.5),),
        position_avg_px=(("BTCUSDT", 65000.0),),
        position_mark_px=(("BTCUSDT", 65100.0),),
    )  # position_sides defaults ()
    hub.apply_snapshot(snap)
    assert hub.account.positions[("binance", "BTCUSDT", "BOTH")] == {"size": 0.5, "avg_px": 65000.0}
    assert hub.account.marks[("binance", "BTCUSDT")] == 65100.0


def test_snapshot_without_avg_falls_back_to_zero():
    hub = _hub()
    snap = ReconcileSnapshot(positions=(("BTCUSDT", 0.5),))  # no avg, no sides
    hub.apply_snapshot(snap)
    assert hub.account.positions[("binance", "BTCUSDT", "BOTH")] == {"size": 0.5, "avg_px": 0.0}
