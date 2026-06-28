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
  ``submit_limit`` / ``submit_market_close`` / ``submit_limit_close`` build the correct
  ``OrderRequest.order_type`` and route to the hub.
  ``submit_stop`` and ``submit_trailing`` both raise ``NotImplementedError`` — stop orders are
  deferred to slice A2e because NO venue client honors ``order_type="stop"`` in
  ``build_order_params`` (every branch only checks ``is_limit``); submitting as-is would fire a
  plain MARKET immediately with the trigger silently dropped — a real-money mis-order.  Raising
  is deliberate: fail safe until A2e wires client-side emulated conditionals.

MTF buffer:
  ``add_live_bar`` / ``bars_for`` / ``forming_for`` mirror ``BacktestEngine`` directly.
  ``parse_timeframe`` and ``resample`` are *imported* from ``core.timeframe`` (the same shared
  helpers BacktestEngine uses) so the resampling logic is not duplicated.  The only live-specific
  difference is that ``_now`` tracks the last bar's ts rather than the backtest loop variable.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Callable

from vike_trader_app.core.bar_buffer import BarSeriesBuffer
from vike_trader_app.core.model import Bar, Position
from vike_trader_app.core.sizing import units_from_percent, units_from_value
from vike_trader_app.exec.conditionals import ConditionalBook
from vike_trader_app.exec.events import OrderRequest

if TYPE_CHECKING:
    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.live_oms import LiveOmsHub


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
        # Shared BarSeriesBuffer — self.bars is passed by reference (not copied).
        self._buf = BarSeriesBuffer(self.bars, timeframes)
        # Client-side emulated conditionals (stop / trailing orders).
        self._book = ConditionalBook()

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

        Mirrors ``BacktestEngine.order_target_value`` via ``units_from_value``:
            target_size = value / (mark * multiplier)
        """
        mark = self._mark()
        if mark <= 0.0:
            return
        self.order_target(units_from_value(value, mark, self._multiplier))

    def order_target_percent(self, pct: float) -> None:
        """Target a fraction of live equity. Mirrors ``BacktestEngine.order_target_percent``
        via ``units_from_percent``:

            target_size = pct * equity_now() / (mark * multiplier)
        """
        mark = self._mark()
        if mark <= 0.0:
            return
        self.order_target(units_from_percent(pct, self.equity_now(), mark, self._multiplier))

    def cancel_all(self) -> None:
        """Cancel every order currently in the hub's registry and clear client-side conditionals."""
        for coid in list(self._hub.registry):
            self._hub.cancel_ticket(coid)
        self._book.clear()

    # ------------------------------------------------------------------
    # Resting-order verbs (limit / stop / trailing / market_close / limit_close)
    # ------------------------------------------------------------------

    def submit_limit(self, side_sign: int, size: float, price: float, weight: float = 0.0) -> None:
        """Submit a resting limit order."""
        del weight
        self._route(self._build_resting_request(side_sign, size, "limit", price))

    def submit_stop(self, side_sign: int, size: float, price: float, weight: float = 0.0) -> None:
        """Register a client-side emulated stop order (A2e).

        Vike emulates stop orders locally: each closed bar is checked by ``check_conditionals``
        and, when ``bar.high >= price`` (buy-stop) or ``bar.low <= price`` (sell-stop), a plain
        MARKET order is submitted via the existing ``submit`` path (which runs the RiskGate).
        No venue client is contacted with a "stop" order type — every venue adapter only checks
        ``is_limit`` in ``build_order_params``, so a native stop would fire as an immediate
        MARKET with the trigger silently dropped (a real-money mis-order).
        """
        self._book.add_stop(side_sign, size, price, weight=weight)

    def submit_trailing(self, side_sign: int, size: float, trail: float, weight: float = 0.0) -> None:
        """Register a client-side emulated trailing-stop order (A2e).

        The trailing extreme is initialised from the current mark price at registration time
        (mirrors ``BacktestEngine.submit_trailing`` which uses ``extreme=self._price``).
        Each closed bar ratchets the extreme via ``order_fill_price`` and fires a MARKET order
        when the retrace crosses the trigger (``extreme - trail`` for sells, ``extreme + trail``
        for buys).
        """
        extreme = self._mark()
        self._book.add_trailing(side_sign, size, trail, extreme=extreme, weight=weight)

    def check_conditionals(self, bar: Bar) -> list:
        """Fire any triggered conditionals for this closed bar.

        Called by ``LiveStrategyPump.feed_bar`` BEFORE ``strategy.on_bar`` so that fills
        precede decisions (matching backtest semantics).  Each fired conditional is submitted
        as a plain MARKET order through the existing ``submit`` path (RiskGate inside the hub).
        Returns the list of fired ``Order`` objects (fire-once: removed from the book).
        """
        fired = self._book.check(bar)
        for o in fired:
            self.submit(o.side, o.size)
        return fired

    def submit_market_close(self, side_sign: int, size: float) -> None:
        """Submit a market order to reduce/close the position (explicit direction + size)."""
        self._route(self._build_request(side_sign, size))

    def submit_limit_close(self, side_sign: int, size: float, price: float) -> None:
        """Submit a limit order to reduce/close the position at the given price."""
        self._route(self._build_resting_request(side_sign, size, "limit", price))

    # ------------------------------------------------------------------
    # Multi-timeframe (MTF) buffer — delegates to BarSeriesBuffer so the
    # logic is not duplicated from BacktestEngine.
    # ------------------------------------------------------------------

    def add_live_bar(self, bar: Bar) -> None:
        """Append a live base bar and refresh higher-TF aggregates (forward mode).

        Updates ``_now`` to the bar's ts, then delegates to ``_buf.add_live_bar``
        (which appends to the shared ``self.bars`` list and re-resamples each
        registered timeframe).
        """
        self._now = bar.ts
        self._buf.add_live_bar(bar)

    def bars_for(self, tf: str):
        """Completed higher-TF bars visible at the current base bar (no look-ahead)."""
        return self._buf.bars_for(tf, self._now)

    def forming_for(self, tf: str):
        """The still-forming coarse bar for ``tf`` from base bars seen so far, or None."""
        return self._buf.forming_for(tf, self._now)
