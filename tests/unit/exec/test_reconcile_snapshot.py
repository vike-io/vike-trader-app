"""5g-3: ReconcileSnapshot carries an additive per-row position_side (default BOTH)."""
from __future__ import annotations

from vike_trader_app.exec.crypto_client import ReconcileSnapshot


def test_default_position_sides_is_empty():
    """Net/spot callers don't pass position_sides -> () (byte-equivalent default)."""
    snap = ReconcileSnapshot(positions=(("BTCUSDT", 0.5),),
                             position_avg_px=(("BTCUSDT", 65000.0),))
    assert snap.position_sides == ()


def test_hedge_snapshot_carries_parallel_sides():
    """A two-leg hedge snapshot indexes position_sides parallel to positions."""
    snap = ReconcileSnapshot(
        positions=(("BTCUSDT", 0.5), ("BTCUSDT", -0.3)),
        position_avg_px=(("BTCUSDT", 65000.0), ("BTCUSDT", 64000.0)),
        position_mark_px=(("BTCUSDT", 65100.0), ("BTCUSDT", 65100.0)),
        position_sides=(("BTCUSDT", "LONG"), ("BTCUSDT", "SHORT")),
    )
    assert snap.position_sides == (("BTCUSDT", "LONG"), ("BTCUSDT", "SHORT"))
    assert len(snap.position_sides) == len(snap.positions)


def test_snapshot_is_frozen_hashable():
    """Frozen dataclass with only tuple fields stays hashable (Qt-metatype safe)."""
    snap = ReconcileSnapshot(positions=(("BTCUSDT", 0.5),))
    hash(snap)  # must not raise
