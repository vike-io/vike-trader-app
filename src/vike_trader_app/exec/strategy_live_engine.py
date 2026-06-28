"""StrategyLiveEngine — routes strategy order verbs to LiveOmsHub over the live Account.

This is the live analogue of ``core.engine.BacktestEngine``: the strategy calls the same
``submit`` / ``submit_close`` / ``order_target`` / ``order_target_value`` / ``order_target_percent``
verbs; here they build an ``OrderRequest`` and hand it to ``LiveOmsHub.submit_ticket`` (which
runs the RiskGate internally — do NOT gate again). Position and equity are read from the live
``Account`` fill-model rather than from a simulation ledger.

Strategy handler firing (``on_order_submitted``, ``on_order_rejected``, etc.) is intentionally
NOT done here.  That is slice A2b's responsibility: A2b subscribes to the real EventBus events
(``OrderSubmitted``, ``OrderDenied``, …) and fires the appropriate handler from the venue's
confirmation.  Firing synchronously here would cause a double-fire once A2b is wired, and would
incorrectly report "submitted" even when the RiskGate vetoes the order (veto publishes
``OrderDenied`` and returns — no raise).

Order-target sizing mirrors BacktestEngine exactly:
  target_size = pct * equity_now() / (mark * multiplier)
The mark comes from ``account.marks.get((venue, symbol))``, the same dict that the perp-mark feed
populates via ``account.set_mark(venue, symbol, px)``.

client_order_id scheme: ``<symbol>-<8-hex-engine-id>-<monotonic-seq>`` — stable prefix per engine
instance, unique per submission, deterministic-enough for dedup / logging without wall-clock
collisions when multiple engines run concurrently on the same symbol.

Resting-order note:
  ``submit_limit`` / ``submit_stop`` / ``submit_market_close`` / ``submit_limit_close`` build the
  correct ``OrderRequest.order_type`` and route to the hub.
  ``submit_trailing`` raises ``NotImplementedError`` — true trailing-stop emulation (tracking the
  extreme and computing the trigger price as the market moves) is deferred to slice A2e.  Raising
  here is deliberate: silently routing a trailing order as a fixed-price stop would mis-order and
  cause real-money harm.

MTF buffer:
  ``add_live_bar`` / ``bars_for`` / ``forming_for`` mirror ``BacktestEngine`` directly.
  ``parse_timeframe`` and ``resample`` are *imported* from ``core.timeframe`` (the same shared
  helpers BacktestEngine uses) so the resampling logic is not duplicated.  The only live-specific
  difference is that ``_now`` tracks the last bar's ts rather than the backtest loop variable.
"""

from __future__ import annotations

import bisect
import os
from operator import attrgetter
from typing import TYPE_CHECKING, Callable

from vike_trader_app.core.model import Bar, Position
from vike_trader_app.core.timeframe import parse_timeframe, resample
from vike_trader_app.exec.events import OrderRequest

if TYPE_CHECKING:
    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.live_oms import LiveOmsHub

_BAR_TS = attrgetter("ts")


def _default_clock() -> int:
    """Wall-clock epoch ms (replaces ``now_ms`` when not injected in production)."""
    import time
    return int(time.time() * 1000)


class StrategyLiveEngine:
    """Seam between a strategy's order verbs and the live LiveOmsHub + Account.

    Parameters
    ----------
    hub:
        ``LiveOmsHub`` (or a duck-compatible stub). Provides ``submit_ticket``,
        ``cancel_ticket``, and ``registry``.
    account:
        ``Account`` fill-model. Provides ``positions``, ``balance``,
        ``unrealized_pnl``, and ``marks``.
    venue, symbol:
        The instrument this engine is wired to.
    multiplier:
        Contract multiplier (1.0 for spot/linear perp, tick-value for futures).
        Applied identically to BacktestEngine so ``order_target_value`` sizing matches.
    now_ms:
        Clock injection. Called on every ``submit``; defaults to wall-clock ms.

    Note: strategy handler callbacks (``on_order_submitted`` etc.) are fired by slice A2b
    via the real EventBus — NOT synchronously here.  See module docstring for rationale.
    """

    def __init__(
        self,
        hub: "LiveOmsHub",
        account: "Account",
        venue: str,
        symbol: str,
        *,
        multiplier: float = 1.0,
        now_ms: Callable[[], int] | None = None,
        timeframes: list[str] | None = None,
    ) -> None:
        self._hub = hub
        self._account = account
        self._venue = venue
        self._symbol = symbol
        self._multiplier = multiplier
        self._now_ms = now_ms if now_ms is not None else _default_clock
        # Per-engine monotonic counter for client_order_id uniqueness.
        self._seq: int = 0
        # Short 8-hex id derived from the process random pool — stable for this engine's lifetime.
        self._engine_tag: str = os.urandom(4).hex()
        # MTF buffer: live bar history + higher-TF aggregates (mirrors BacktestEngine).
        self.bars: list[Bar] = []
        # _now tracks the ts of the last live bar fed (used by bars_for / forming_for slicing).
        self._now: int = 0
        # tf -> (target_ms, [coarse bars]); populated for each requested timeframe.
        self._tf: dict[str, tuple[int, list]] = {}
        for tf in timeframes or []:
            ms = parse_timeframe(tf)
            self._tf[tf] = (ms, [])

    # ------------------------------------------------------------------
    # Read-model helpers (mirror BacktestEngine.position / equity_now)
    # ------------------------------------------------------------------

    @property
    def position(self) -> Position:
        """Current position from the live Account, as a ``core.model.Position``."""
        raw = self._account.positions.get((self._venue, self._symbol, "BOTH"), {})
        return Position(size=raw.get("size", 0.0), avg_price=raw.get("avg_px", 0.0))

    def equity_now(self) -> float:
        """Cash balance + unrealized PnL on this instrument (mirrors BacktestEngine.equity_now)."""
        return self._account.balance + self._account.unrealized_pnl(self._venue, self._symbol)

    def drawdown_now(self) -> float:
        """Drawdown from the running equity peak (0.0 if at or above peak).

        NOTE: The live engine doesn't track a running peak (that's the risk-gate's job);
        returns 0.0 as a conservative no-op until wired to a peak tracker in a later slice.
        """
        return 0.0

    @property
    def now(self) -> int:
        """Current time (epoch ms) from the injected clock."""
        return self._now_ms()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_coid(self) -> str:
        """Return a unique client_order_id: ``<symbol>-<engine-tag>-<seq>``."""
        self._seq += 1
        return f"{self._symbol}-{self._engine_tag}-{self._seq}"

    def _mark(self) -> float:
        """Latest mark price for this instrument from the Account marks dict.

        The perp mark feed calls ``account.set_mark(venue, symbol, px)`` which writes
        ``account.marks[(venue, symbol)]``. For spot, the last fill price serves as the
        proxy mark; callers of ``order_target_percent`` / ``order_target_value`` must
        ensure a mark is present before calling (same precondition as BacktestEngine._price
        which is set from the active bar's open).
        """
        return self._account.marks.get((self._venue, self._symbol), 0.0)

    def _build_request(self, side: int, qty: float) -> OrderRequest:
        return OrderRequest(
            client_order_id=self._next_coid(),
            venue=self._venue,
            symbol=self._symbol,
            side=side,
            qty=qty,
            order_type="market",
            price=None,
            ts=self._now_ms(),
        )

    def _build_resting_request(
        self, side: int, qty: float, order_type: str, price: float | None
    ) -> OrderRequest:
        return OrderRequest(
            client_order_id=self._next_coid(),
            venue=self._venue,
            symbol=self._symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            price=price,
            ts=self._now_ms(),
        )

    def _route(self, req: OrderRequest) -> None:
        """Submit to hub. RiskGate is INSIDE submit_ticket — do NOT gate here.

        Handler firing (on_order_submitted / on_order_rejected / …) is A2b's job, driven by
        the real EventBus events so it fires only on actual venue confirmation and a RiskGate
        veto fires on_order_rejected instead.  Do NOT add handler calls here.
        """
        self._hub.submit_ticket(req)

    # ------------------------------------------------------------------
    # Order verbs (mirror BacktestEngine's public surface)
    # ------------------------------------------------------------------

    def submit(self, side_sign: int, size: float, weight: float = 0.0, stop=None) -> None:
        """Submit a market order. ``stop`` and ``weight`` are accepted for API parity, ignored here."""
        del stop, weight
        if size > 0.0:
            self._route(self._build_request(side_sign, size))

    def submit_close(self) -> None:
        """Flatten the current position with a market order (no-op if already flat)."""
        pos = self.position
        if pos.size != 0.0:
            side = -1 if pos.size > 0.0 else 1
            self._route(self._build_request(side, abs(pos.size)))

    def order_target(self, target_size: float) -> None:
        """Market order to move the position to ``target_size`` signed units.

        Mirrors ``BacktestEngine.order_target`` exactly:
            delta = target_size - position.size
            if delta > 0: submit buy delta
            if delta < 0: submit sell abs(delta)
        """
        delta = target_size - self.position.size
        if delta > 0:
            self.submit(+1, delta)
        elif delta < 0:
            self.submit(-1, -delta)

    def order_target_value(self, value: float) -> None:
        """Target a notional value. Converts via the current mark price and multiplier.

        Mirrors ``BacktestEngine.order_target_value``:
            target_size = value / (mark * multiplier)
        """
        mark = self._mark()
        if mark <= 0.0:
            return
        self.order_target(value / (mark * self._multiplier))

    def order_target_percent(self, pct: float) -> None:
        """Target a fraction of live equity. Mirrors ``BacktestEngine.order_target_percent``:

            target_size = pct * equity_now() / (mark * multiplier)
        """
        mark = self._mark()
        if mark <= 0.0:
            return
        self.order_target(pct * self.equity_now() / (mark * self._multiplier))

    def cancel_all(self) -> None:
        """Cancel every order currently in the hub's registry."""
        for coid in list(self._hub.registry):
            self._hub.cancel_ticket(coid)

    # ------------------------------------------------------------------
    # Resting-order verbs (limit / stop / trailing / market_close / limit_close)
    # ------------------------------------------------------------------

    def submit_limit(self, side_sign: int, size: float, price: float, weight: float = 0.0) -> None:
        """Submit a resting limit order."""
        del weight
        self._route(self._build_resting_request(side_sign, size, "limit", price))

    def submit_stop(self, side_sign: int, size: float, price: float, weight: float = 0.0) -> None:
        """Submit a stop order at the given trigger price."""
        del weight
        self._route(self._build_resting_request(side_sign, size, "stop", price))

    def submit_trailing(self, side_sign: int, size: float, trail: float, weight: float = 0.0) -> None:
        """Trailing stops are emulated in slice A2e.

        A2a does NOT silently mis-route a trailing order as a fixed-price stop (that would
        require tracking the running extreme and recomputing the trigger as the market moves).
        Raising ``NotImplementedError`` here is deliberate: a silent mis-order causes real-money
        harm.  Wire slice A2e to replace this stub.
        """
        raise NotImplementedError(
            "submit_trailing: trailing-stop emulation (extreme tracking + dynamic trigger) "
            "is deferred to slice A2e.  Use submit_stop with an explicit price for now."
        )

    def submit_market_close(self, side_sign: int, size: float) -> None:
        """Submit a market order to reduce/close the position (explicit direction + size)."""
        self._route(self._build_request(side_sign, size))

    def submit_limit_close(self, side_sign: int, size: float, price: float) -> None:
        """Submit a limit order to reduce/close the position at the given price."""
        self._route(self._build_resting_request(side_sign, size, "limit", price))

    # ------------------------------------------------------------------
    # Multi-timeframe (MTF) buffer — mirrors BacktestEngine.add_live_bar /
    # bars_for / forming_for using the shared core.timeframe helpers.
    # ------------------------------------------------------------------

    def add_live_bar(self, bar: Bar) -> None:
        """Append a live base bar and refresh higher-TF aggregates (forward mode).

        Mirrors ``BacktestEngine.add_live_bar`` exactly: appends to ``self.bars`` and
        re-resamples each registered timeframe.  ``_now`` is updated to the bar's ts so
        that ``bars_for``/``forming_for`` slice correctly without look-ahead.
        """
        self.bars.append(bar)
        self._now = bar.ts
        for tf, (ms, _) in list(self._tf.items()):
            self._tf[tf] = (ms, resample(self.bars, ms))

    def bars_for(self, tf: str):
        """Completed higher-TF bars visible at the current base bar (no look-ahead).

        Mirrors ``BacktestEngine.bars_for``: slices the coarse list up to (but not
        including) the window that contains ``_now``.
        """
        ms, coarse = self._tf[tf]
        window_start = self._now - self._now % ms
        return coarse[:bisect.bisect_left(coarse, window_start, key=_BAR_TS)]

    def forming_for(self, tf: str):
        """The still-building coarse bar for ``tf`` from base bars seen so far, or None.

        Mirrors ``BacktestEngine.forming_for``: aggregates the base bars in the current
        higher-TF window into a synthetic Bar.
        """
        ms, _ = self._tf[tf]
        window_start = self._now - self._now % ms
        lo = bisect.bisect_left(self.bars, window_start, key=_BAR_TS)
        hi = bisect.bisect_right(self.bars, self._now, key=_BAR_TS)
        window = self.bars[lo:hi]
        if not window:
            return None
        return Bar(
            ts=window_start,
            open=window[0].open,
            high=max(b.high for b in window),
            low=min(b.low for b in window),
            close=window[-1].close,
            volume=sum(b.volume for b in window),
        )
