"""The ONE Strategy API — symbol-explicit, event-driven.

Subclass and implement ``on_bar(bar)``; ``bar.symbol`` carries the
"SYMBOL.VENUE" instrument id.  Runs unchanged on 1..N symbols.
Place orders with symbol-first verbs that return an ``OrderHandle``.
"""
from __future__ import annotations

import logging

from .model import Bar, Position
from .order_handle import OrderHandle, _alloc_id
from .schedule import Schedule

# Re-export the compat shim so strategy source strings can do:
#   from vike_trader_app.core.strategy import SingleSymbolStrategy
# without needing a separate compat_strategy allowlist entry in the preflight gate.
from .compat_strategy import SingleSymbolStrategy  # noqa: F401, E402

logger = logging.getLogger(__name__)


class Strategy:
    """Unified per-symbol strategy base class.

    Engine-facing ``_on_step(ts, cur)`` fans the bar bundle out to the user's
    ``on_bar(bar)`` — once per symbol per step.  All order verbs are
    symbol-explicit and return an ``OrderHandle`` (or ``None`` if nothing was
    placed).  Reads are keyed by the same symbol string.
    """

    PARAM_GRID: dict = {}
    WARMUP: int = 0

    def __init__(self) -> None:
        self._engine = None   # injected by PortfolioEngine.__init__
        self.index: int = 0
        self.schedule: Schedule = Schedule()

    @classmethod
    def make(cls, **params):
        """Factory: create an instance and stamp ``params`` as attributes."""
        inst = cls()
        for k, v in params.items():
            setattr(inst, k, v)
        return inst

    # ------------------------------------------------------------------
    # Metadata / class-level
    # ------------------------------------------------------------------

    @property
    def symbols(self) -> list[str]:
        """Raw symbol keys as held by the engine (bare, not venue-qualified)."""
        return list(self._engine.symbols)

    # ------------------------------------------------------------------
    # Engine-facing dispatch: fan the bundle out per symbol
    # ------------------------------------------------------------------

    def _on_step(self, ts: int, bars: dict) -> None:
        for _sym, bar in bars.items():
            self.on_bar(bar)

    # ------------------------------------------------------------------
    # THE handler — override this
    # ------------------------------------------------------------------

    def on_bar(self, bar: Bar) -> None:  # noqa: ARG002
        """Called once per bar per symbol.  ``bar.symbol`` is "SYM.VENUE"."""

    # ------------------------------------------------------------------
    # Reserved for P2/P3 — no-op defaults; DO NOT override yet
    # ------------------------------------------------------------------

    def on_quote_tick(self, q) -> None:  # noqa: ARG002
        """Reserved: L1 quote tick handler (wired in P2)."""

    def on_trade_tick(self, t) -> None:  # noqa: ARG002
        """Reserved: trade tick handler (wired in P2)."""

    def on_order_book(self, ob) -> None:  # noqa: ARG002
        """Reserved: L2 order book handler (wired in P3)."""

    # ------------------------------------------------------------------
    # Lifecycle — no-op defaults (firing wired in Task 8)
    # ------------------------------------------------------------------

    def on_start(self) -> None: ...
    def on_stop(self) -> None: ...
    def on_fill(self, fill) -> None: ...
    def on_order_submitted(self, e) -> None: ...
    def on_order_accepted(self, e) -> None: ...
    def on_order_rejected(self, e) -> None: ...
    def on_order_canceled(self, e) -> None: ...
    def on_order_filled(self, e) -> None: ...
    def on_liquidation(self, e) -> None: ...

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sym_key(self, symbol: str) -> str:
        """Map a (possibly venue-qualified) instrument id to the engine's bare key.

        Bar.symbol is "BTC.BINANCE"; the engine keys its state by "BTC".
        """
        return symbol.split(".")[0] if "." in symbol else symbol

    def _wrap(self, symbol: str, order) -> OrderHandle | None:
        """Wrap an engine Order in an OrderHandle (or return None)."""
        if order is None:
            return None
        return OrderHandle(_alloc_id(), order, self._engine, self._sym_key(symbol))

    # ------------------------------------------------------------------
    # Order verbs — symbol-first, return OrderHandle
    # ------------------------------------------------------------------

    def buy(
        self,
        symbol: str,
        size: float,
        *,
        limit: float | None = None,
        stop: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        tif: str = "GTC",
        submit: bool = True,
    ) -> OrderHandle | None:
        """Open or add to a long position."""
        return self._place(symbol, +1, size, limit, stop, stop_loss, take_profit, tif, submit)

    def sell(
        self,
        symbol: str,
        size: float,
        *,
        limit: float | None = None,
        stop: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        tif: str = "GTC",
        submit: bool = True,
    ) -> OrderHandle | None:
        """Open or add to a short position."""
        return self._place(symbol, -1, size, limit, stop, stop_loss, take_profit, tif, submit)

    def _place(
        self,
        symbol: str,
        side: int,
        size: float,
        limit: float | None,
        stop: float | None,
        stop_loss: float | None,
        take_profit: float | None,  # noqa: ARG002 — P1: documented deferral, not wired yet
        tif: str,  # noqa: ARG002 — P1: engine doesn't yet enforce TIF
        do_submit: bool,
    ) -> OrderHandle | _UnsentOrder | None:
        key = self._sym_key(symbol)
        if not do_submit:
            return _UnsentOrder(self, key, side, size, limit, stop, stop_loss)
        if limit is not None:
            # submit_limit accepts stop= for arming a protective stop on fill
            o = self._engine.submit_limit(key, side, size, limit, stop=stop_loss)
        elif stop is not None:
            # submit_stop does NOT accept stop=; stop_loss is ignored here (doc gap)
            o = self._engine.submit_stop(key, side, size, stop)
        else:
            # plain market; stop_loss arms a protective stop via stop= kwarg
            o = self._engine.submit(key, side, size, stop=stop_loss)
        # take_profit as a resting TP limit is added when rung-3 lifecycle lands (P1: no-op).
        return self._wrap(symbol, o)

    def close(self, symbol: str) -> None:
        """Market-close the full position in ``symbol``."""
        self._engine.submit_close(self._sym_key(symbol))

    # ------------------------------------------------------------------
    # Target-weight / rebalance verbs
    # ------------------------------------------------------------------

    def order_target_percent(self, symbol: str, pct: float) -> None:
        """Drive ``symbol`` to ``pct`` fraction of current equity."""
        self._engine_target(symbol, "percent", pct)

    def order_target_shares(self, symbol: str, qty: float) -> None:
        """Drive ``symbol`` to an absolute share count."""
        self._engine_target(symbol, "shares", qty)

    def order_target_value(self, symbol: str, value: float) -> None:
        """Drive ``symbol`` to a notional $ value."""
        self._engine_target(symbol, "value", value)

    def _engine_target(self, symbol: str, kind: str, x: float) -> None:
        key = self._sym_key(symbol)
        pos = self._engine.position_of(key).size
        price = self._engine.price_of(key)
        if kind == "percent":
            target = x * self._engine.equity_now() / price if price else 0.0
        elif kind == "value":
            target = x / price if price else 0.0
        else:  # "shares"
            target = x
        delta = target - pos
        if abs(delta) > 1e-12:
            self._engine.submit(key, 1 if delta > 0 else -1, abs(delta), raw=True)

    def rebalance(self, weights: dict) -> None:
        """Target each ``{symbol: weight}`` as a fraction of current equity."""
        for symbol, w in weights.items():
            self.order_target_percent(symbol, w)

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def cancel_all(self, symbol: str) -> None:
        """Cancel all pending orders for ``symbol``."""
        self._engine.cancel_all(self._sym_key(symbol))

    def orders(self, symbol: str | None = None) -> list:
        """Return all pending orders; filter by symbol if given."""
        keys = [self._sym_key(symbol)] if symbol else list(self._engine.symbols)
        return [o for k in keys for o in self._engine._pending_of(k)]

    def submit(self, order_or_list, *, link=None) -> list:  # noqa: ARG002 — link wired in P2
        """Send a list of _UnsentOrder objects built with submit=False."""
        items = order_or_list if isinstance(order_or_list, list) else [order_or_list]
        return [it._send() for it in items]

    # ------------------------------------------------------------------
    # Reads — symbol-keyed
    # ------------------------------------------------------------------

    def position(self, symbol: str) -> Position:
        """Current ``Position`` for ``symbol``."""
        return self._engine.position_of(self._sym_key(symbol))

    def price(self, symbol: str) -> float:
        """Last seen price for ``symbol``."""
        return self._engine.price_of(self._sym_key(symbol))

    def bars(self, symbol: str, tf: str):
        """Completed higher-TF bars visible at the current step (no look-ahead)."""
        return self._engine.bars_for(self._sym_key(symbol), tf)

    def forming(self, symbol: str, tf: str):
        """The still-building coarse bar for ``tf`` / ``symbol``, or None."""
        return self._engine.forming_for(self._sym_key(symbol), tf)

    def history(self, symbol: str, interval: str, count: int):
        """Higher-timeframe history — NOT yet wired on the unified engine (follow-up slice)."""
        raise NotImplementedError(
            "history() is not yet wired on the unified portfolio engine — follow-up slice. "
            "Use self.bars(symbol, tf) for higher-timeframe reads within the run window."
        )

    # ------------------------------------------------------------------
    # Account reads
    # ------------------------------------------------------------------

    @property
    def equity(self) -> float:
        """Current account equity (cash + open position mark-to-market)."""
        return self._engine.equity_now()

    @property
    def drawdown(self) -> float:
        """Current drawdown from the running equity peak (0..1)."""
        return self._engine.drawdown_now()

    # ------------------------------------------------------------------
    # Chart / UI hook
    # ------------------------------------------------------------------

    def chart_overlays(self, closes) -> dict:
        """Return overlay series for the chart (override to add indicator lines)."""
        return {}


class _UnsentOrder:
    """Returned by ``buy/sell(..., submit=False)``; sent via ``self.submit([...])``.

    Holds enough context to dispatch the actual engine call when ``_send()`` is
    called.  link="OCO" native grouping is wired in P2.
    """

    def __init__(
        self,
        strat: Strategy,
        key: str,
        side: int,
        size: float,
        limit: float | None,
        stop: float | None,
        stop_loss: float | None,
    ) -> None:
        self._strat = strat
        self._key = key
        self._side = side
        self._size = size
        self._limit = limit
        self._stop = stop
        self._sl = stop_loss

    def _send(self) -> OrderHandle | None:
        return self._strat._place(
            self._key,
            self._side,
            self._size,
            self._limit,
            self._stop,
            self._sl,
            None,   # take_profit: no-op in P1
            "GTC",
            do_submit=True,
        )
