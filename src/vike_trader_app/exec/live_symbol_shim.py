"""LiveSymbolShim — SingleSymbolStrategy compat over the unified LiveEngine.

Presents the unkeyed ``StrategyEngine`` Protocol surface over a symbol-keyed
``LiveEngine`` for one symbol.  Every method simply forwards to the keyed
``LiveEngine`` verb with ``self._symbol`` prepended — there is NO logic here.

This is the live analogue of ``core.portfolio_adapter.SymbolEngineShim``.
The backtest shim wraps ``PortfolioEngine``; this wraps ``LiveEngine``.

Usage::

    engine = LiveEngine(hubs, account)
    strat  = MySingleSymbolStrategy()
    strat._engine = LiveSymbolShim(engine, "BTCUSDT")

``SingleSymbolStrategy`` reads ``self._engine.position`` / ``equity_now()`` and
calls ``submit`` / ``submit_close`` / ``order_target_*`` — all of which are
satisfied by this shim, symbol-bound to ``"BTCUSDT"`` (or whatever symbol was
passed at construction time).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from vike_trader_app.core.model import Bar
    from vike_trader_app.exec.live_portfolio_engine import LiveEngine


class LiveSymbolShim:
    """Unkeyed ``StrategyEngine`` surface bound to one symbol on a ``LiveEngine``.

    Parameters
    ----------
    engine:
        The shared, symbol-keyed ``LiveEngine`` instance.
    symbol:
        The symbol this shim is bound to (e.g. ``"BTCUSDT"``).
    """

    def __init__(self, engine: "LiveEngine", symbol: str) -> None:
        self._engine = engine
        self._symbol = symbol

    # ------------------------------------------------------------------
    # Read model — mirrors SymbolEngineShim reads
    # ------------------------------------------------------------------

    @property
    def position(self):
        """Current position for this symbol (``Position(size, avg_price)``)."""
        return self._engine.position_of(self._symbol)

    def position_of(self, symbol: str):  # SymbolEngineShim compat — passed symbol ignored
        """Return position for this shim's bound symbol (ignores the passed symbol arg)."""
        return self._engine.position_of(self._symbol)

    def price_of(self, symbol: str) -> float:  # SymbolEngineShim compat — passed symbol ignored
        """Return latest mark price for this shim's bound symbol (ignores the passed symbol arg)."""
        return self._engine.price_of(self._symbol)

    @property
    def now(self) -> int:
        """Current time (epoch ms) from the engine's injected clock."""
        return self._engine.now

    @property
    def price(self) -> float:
        """Latest mark price for this symbol."""
        return self._engine.price_of(self._symbol)

    def _mark(self) -> float:
        """Return the current mark price (alias used by some internal paths)."""
        return self._engine.price_of(self._symbol)

    def equity_now(self) -> float:
        """Total live equity (cash + unrealized PnL across ALL symbols)."""
        return self._engine.equity_now()

    def drawdown_now(self) -> float:
        """Current drawdown from the running peak (0.0 stub; mirrors LiveEngine)."""
        return self._engine.drawdown_now()

    @property
    def symbols(self) -> list:
        """Single-element list containing this shim's bound symbol."""
        return [self._symbol]

    # ------------------------------------------------------------------
    # Market / target orders
    # ------------------------------------------------------------------

    def submit(
        self,
        side_sign: int,
        size: float,
        weight: float = 0.0,
        stop=None,
        raw: bool = False,
    ) -> None:
        """Forward an unkeyed market order to the bound symbol.

        Mirrors ``SymbolEngineShim.submit`` — the old-API call form
        (``submit(side, size, ...)``) maps straight through with ``self._symbol``.
        ``weight``, ``stop``, and ``raw`` are forwarded for signature-parity but
        are ignored inside ``LiveEngine.submit`` (the live path routes the explicit
        size directly — no sizer).
        """
        self._engine.submit(self._symbol, side_sign, size, weight=weight, raw=raw, stop=stop)

    def submit_close(self, symbol: str | None = None) -> None:  # symbol ignored
        """Flatten the position in this symbol (market order, no-op if flat)."""
        self._engine.submit_close(self._symbol)

    def order_target(self, target: float) -> None:
        """Market order to move this symbol's position to ``target`` signed units.

        Forwards to ``LiveEngine.order_target(sym, target)`` which computes the delta
        and calls ``submit`` with ``raw=True`` — matching ``SymbolEngineShim``'s
        explicit-qty semantics exactly.
        """
        self._engine.order_target(self._symbol, target)

    def order_target_value(self, value: float) -> None:
        """Target a notional value for this symbol.

        Forwards to ``LiveEngine.order_target_value(sym, value)`` which converts via
        the current mark price and multiplier before calling ``order_target``.
        """
        self._engine.order_target_value(self._symbol, value)

    def order_target_percent(self, pct: float) -> None:
        """Target a fraction of live equity for this symbol.

        Forwards to ``LiveEngine.order_target_percent(sym, pct)`` which resolves
        the target via ``units_from_percent`` then calls ``order_target`` (with
        implicit ``raw=True`` inside ``order_target → submit``).
        """
        self._engine.order_target_percent(self._symbol, pct)

    # ------------------------------------------------------------------
    # Resting orders — forwarded verbatim
    # ------------------------------------------------------------------

    def submit_limit(
        self,
        side_sign: int,
        size: float,
        price: float,
        weight: float = 0.0,
        stop=None,
    ) -> None:
        """Forward a limit order to this symbol."""
        self._engine.submit_limit(self._symbol, side_sign, size, price, weight=weight, stop=stop)

    def submit_stop(
        self,
        side_sign: int,
        size: float,
        price: float,
        weight: float = 0.0,
    ) -> None:
        """Register a client-side emulated stop order for this symbol."""
        self._engine.submit_stop(self._symbol, side_sign, size, price, weight=weight)

    def submit_trailing(
        self,
        side_sign: int,
        size: float,
        trail: float,
        weight: float = 0.0,
    ) -> None:
        """Register a client-side emulated trailing-stop order for this symbol."""
        self._engine.submit_trailing(self._symbol, side_sign, size, trail, weight=weight)

    def submit_market_close(self, side_sign: int, size: float, weight: float = 0.0) -> None:
        """Market close (explicit direction + size) for this symbol."""
        self._engine.submit_market_close(self._symbol, side_sign, size, weight=weight)

    def submit_limit_close(
        self, side_sign: int, size: float, price: float, weight: float = 0.0
    ) -> None:
        """Limit close at ``price`` for this symbol."""
        self._engine.submit_limit_close(self._symbol, side_sign, size, price, weight=weight)

    def cancel_all(self, symbol: str | None = None) -> None:  # symbol ignored
        """Cancel every resting order and clear the conditional book for this symbol."""
        self._engine.cancel_all(self._symbol)

    # ------------------------------------------------------------------
    # Multi-timeframe reads
    # ------------------------------------------------------------------

    def bars_for(self, symbol_or_tf, tf: str | None = None):
        """Completed higher-TF bars for this symbol at the current step (no look-ahead).

        Compat: old ``bars_for(tf)`` / new ``bars_for(symbol, tf)`` — mirrors
        ``SymbolEngineShim.bars_for``.  The passed symbol is always ignored; this shim
        always routes to ``self._symbol``.
        """
        _tf = tf if tf is not None else symbol_or_tf
        return self._engine.bars_for(self._symbol, _tf)

    def forming_for(self, symbol_or_tf, tf: str | None = None):
        """The still-forming coarse bar for ``tf`` / this symbol, or None.

        Compat: old ``forming_for(tf)`` / new ``forming_for(symbol, tf)`` — mirrors
        ``SymbolEngineShim.forming_for``.  The passed symbol is always ignored.
        """
        _tf = tf if tf is not None else symbol_or_tf
        return self._engine.forming_for(self._symbol, _tf)

    # ------------------------------------------------------------------
    # Bar buffer + conditionals
    # ------------------------------------------------------------------

    def add_live_bar(self, bar: "Bar") -> None:
        """Append a live bar for this symbol to the engine's buffer + update the mark."""
        self._engine.add_live_bar(self._symbol, bar)

    def check_conditionals(self, bar: "Bar") -> list:
        """Fire any triggered conditionals for this symbol against the closed bar."""
        return self._engine.check_conditionals(self._symbol, bar)
