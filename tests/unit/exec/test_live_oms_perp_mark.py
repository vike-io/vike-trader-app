"""LiveOmsHub: mark-price feed into Account (unrealized PnL) via fills + apply_snapshot.

Task 4 — two mark-price sources:
  1. per-fill markPrice: FillEvent.mark_price -> account.set_mark (when mark > 0.0)
  2. reconcile: apply_snapshot position_mark_px -> account.set_mark (when mark > 0.0)

CRITIC: skip set_mark when mark <= 0.0 (flat perp reconcile sentinel + None/0 fills).
"""

from __future__ import annotations

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.crypto_client import ReconcileSnapshot
from vike_trader_app.exec.events import FillEvent
from vike_trader_app.exec.live_oms import LiveOmsHub
from vike_trader_app.exec.risk import RiskGate, RiskLimits


def _hub():
    bus = EventBus()
    return bus, LiveOmsHub(bus=bus, account=Account(), gate=RiskGate(RiskLimits()),
                           client=object(), venue="bybit", symbol="BTCUSDT")


# ---------------------------------------------------------------------------
# per-fill mark feed
# ---------------------------------------------------------------------------

def test_fill_with_mark_price_feeds_set_mark_and_unrealized_pnl():
    bus, hub = _hub()
    bus.publish(FillEvent(trade_id="e1", client_order_id="p-0", venue="bybit", symbol="BTCUSDT",
                          side=+1, last_qty=0.01, last_px=65000.0, mark_price=65100.0,
                          position_side="BOTH"))
    pos = hub.account.positions[("bybit", "BTCUSDT", "BOTH")]
    assert pos["size"] == 0.01 and pos["avg_px"] == 65000.0
    # mark recorded -> unrealized PnL = (65100 - 65000) * 0.01 = 1.0
    assert hub.account.unrealized_pnl("bybit", "BTCUSDT") == 1.0


def test_fill_without_mark_price_leaves_unrealized_zero():
    bus, hub = _hub()
    bus.publish(FillEvent(trade_id="e2", client_order_id="p-1", venue="bybit", symbol="BTCUSDT",
                          side=+1, last_qty=0.01, last_px=65000.0, mark_price=None))
    # fill folded but no mark set -> unrealized_pnl returns 0.0
    assert hub.account.unrealized_pnl("bybit", "BTCUSDT") == 0.0


def test_fill_with_mark_price_zero_does_not_set_mark():
    """mark_price=0.0 is a sentinel (not a real price) — must not pollute account.marks."""
    bus, hub = _hub()
    bus.publish(FillEvent(trade_id="e3", client_order_id="p-2", venue="bybit", symbol="BTCUSDT",
                          side=+1, last_qty=0.01, last_px=65000.0, mark_price=0.0))
    assert ("bybit", "BTCUSDT") not in hub.account.marks
    assert hub.account.unrealized_pnl("bybit", "BTCUSDT") == 0.0


def test_fill_with_negative_mark_does_not_set_mark():
    """Negative mark price is always bogus — guard the same way."""
    bus, hub = _hub()
    bus.publish(FillEvent(trade_id="e4", client_order_id="p-3", venue="bybit", symbol="BTCUSDT",
                          side=+1, last_qty=0.01, last_px=65000.0, mark_price=-1.0))
    assert ("bybit", "BTCUSDT") not in hub.account.marks


def test_fill_mark_updates_on_subsequent_fills():
    """A second fill with a different mark overwrites the stored mark."""
    bus, hub = _hub()
    bus.publish(FillEvent(trade_id="e5", client_order_id="p-4", venue="bybit", symbol="BTCUSDT",
                          side=+1, last_qty=0.01, last_px=65000.0, mark_price=65100.0))
    bus.publish(FillEvent(trade_id="e6", client_order_id="p-5", venue="bybit", symbol="BTCUSDT",
                          side=+1, last_qty=0.01, last_px=65200.0, mark_price=65300.0))
    assert hub.account.marks[("bybit", "BTCUSDT")] == 65300.0


# ---------------------------------------------------------------------------
# apply_snapshot mark feed
# ---------------------------------------------------------------------------

def test_apply_snapshot_seeds_mark_from_position_mark_px():
    bus, hub = _hub()
    hub.apply_snapshot(ReconcileSnapshot(positions=(("BTCUSDT", -0.2),),
                                         position_avg_px=(("BTCUSDT", 65000.0),),
                                         position_mark_px=(("BTCUSDT", 64900.0),)))
    # short 0.2 @ 65000, mark 64900 -> unrealized = (64900 - 65000) * -0.2 = +20.0
    assert hub.account.unrealized_pnl("bybit", "BTCUSDT") == 20.0


def test_apply_snapshot_zero_mark_does_not_set_mark():
    """position_mark_px=(("BTCUSDT", 0.0),) is the flat-perp sentinel — must not pollute marks."""
    bus, hub = _hub()
    hub.apply_snapshot(ReconcileSnapshot(positions=(("BTCUSDT", 0.0),),
                                         position_avg_px=(("BTCUSDT", 0.0),),
                                         position_mark_px=(("BTCUSDT", 0.0),)))
    assert ("bybit", "BTCUSDT") not in hub.account.marks


def test_apply_snapshot_empty_position_mark_px_is_noop():
    """Spot snapshots have no position_mark_px — must not crash and must not set any mark."""
    bus, hub = _hub()
    hub.apply_snapshot(ReconcileSnapshot(positions=(("BTCUSDT", 0.5),),
                                         position_avg_px=(("BTCUSDT", 68000.0),)))
    assert ("bybit", "BTCUSDT") not in hub.account.marks


def test_apply_snapshot_positive_mark_overwrites_previous():
    """Two consecutive snapshots — second mark wins."""
    bus, hub = _hub()
    hub.apply_snapshot(ReconcileSnapshot(positions=(("BTCUSDT", 0.1),),
                                         position_avg_px=(("BTCUSDT", 65000.0),),
                                         position_mark_px=(("BTCUSDT", 65100.0),)))
    hub.apply_snapshot(ReconcileSnapshot(positions=(("BTCUSDT", 0.1),),
                                         position_avg_px=(("BTCUSDT", 65000.0),),
                                         position_mark_px=(("BTCUSDT", 65200.0),)))
    assert hub.account.marks[("bybit", "BTCUSDT")] == 65200.0


# ---------------------------------------------------------------------------
# spot unaffected (regression guard)
# ---------------------------------------------------------------------------

def test_spot_fill_no_mark_price_does_not_touch_marks():
    """Spot fills always have mark_price=None -> marks dict stays empty."""
    bus = EventBus()
    hub = LiveOmsHub(bus=bus, account=Account(), gate=RiskGate(RiskLimits()),
                     client=object(), venue="binance", symbol="BTCUSDT")
    bus.publish(FillEvent(trade_id="s1", client_order_id="x-0", venue="binance",
                          symbol="BTCUSDT", side=+1, last_qty=1.0, last_px=65000.0,
                          mark_price=None))
    assert hub.account.marks == {}
    assert hub.account.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 1.0
