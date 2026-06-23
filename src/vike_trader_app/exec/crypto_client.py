"""Venue-agnostic crypto spot execution base + the shared reconcile snapshot and error type.

CryptoExecutionClient (added in Task 2) owns the FLOW (every bus.publish + the transport call)
and delegates five venue seams to hooks. ReconcileSnapshot and VenueApiError live here so both
Binance and Bybit subclasses share them. BinanceApiError/BybitApiError subclass VenueApiError so
the shared submit()/cancel() try/except is identical across venues (Binance raises in the transport
on HTTP-4xx; Bybit raises in unwrap() on a 200-body retCode!=0).
"""

from __future__ import annotations

from dataclasses import dataclass

from vike_trader_app.exec.order import ManagedOrder


class VenueApiError(RuntimeError):
    """A venue order/account error, normalized to (code, msg). Subclassed per venue."""

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(f"venue error {code}: {msg}")
        self.code = code
        self.msg = msg


@dataclass(frozen=True)
class ReconcileSnapshot:
    positions: tuple[tuple[str, float], ...] = ()
    open_orders: tuple[ManagedOrder, ...] = ()
    # Per-position mark price at reconcile time — seeded as avg_px so an immediate close is ~0 PnL
    # instead of garbage (true cost basis is unknown for a pre-existing holding).
    position_avg_px: tuple[tuple[str, float], ...] = ()
