"""LivePortfolioEngine — multi-symbol live engine interface for PortfolioStrategy (A2d).

This is the live analogue of ``core.portfolio.PortfolioEngine``: a ``PortfolioStrategy``
sets ``strategy._engine = LivePortfolioEngine(...)`` and calls the EXACT same surface it
calls in backtest — ``equity_now``, ``position_of``, ``price_of``, ``submit``,
``submit_close``, ``symbols``, and ``now``.

Per-symbol routing mirrors ``StrategyLiveEngine``: each verb resolves
``hub = self.hubs[sym]`` then builds an ``OrderRequest`` and hands it to
``hub.submit_ticket`` (RiskGate is inside the hub — do NOT gate here).

Key design decisions (mirror the plan):
- ``submit``'s ``weight`` / ``raw`` / ``stop`` are accepted for signature-parity ONLY.
  The strategy already computed the desired delta via ``order_target_percent`` / ``buy`` /
  ``sell``; we route the EXPLICIT ``size`` as a plain market order.  NO backtest sizer or
  leverage cap is applied here.
- ``equity_now()`` = ``account.balance + sum(account.unrealized_pnl(venue, sym) for sym)``.
  The shared ``Account`` is keyed ``(venue, symbol, position_side)`` — naturally multi-symbol.
- One ``BarSeriesBuffer`` per symbol (not shared across symbols); ``add_live_bar(sym, bar)``
  appends to that symbol's buffer and calls ``account.set_mark`` so the equity read is
  mark-accurate (the A2c spot-mark fix, extended per symbol).
- ``submit_stop`` / ``submit_trailing`` register client-side emulated conditionals (A2e):
  a ``ConditionalBook`` per symbol; ``check_conditionals(sym, bar)`` fires triggered
  conditionals as plain MARKET orders through the existing ``submit`` path.
- Qt-free.  All state is plain Python.

``client_order_id`` scheme: ``<symbol>-<8-hex-engine-id>-<monotonic-seq>`` — stable prefix
per engine instance, unique per submission, collision-safe when N engines share a venue.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Callable

from vike_trader_app.core.bar_buffer import BarSeriesBuffer
from vike_trader_app.core.model import Bar, Position
from vike_trader_app.exec.conditionals import ConditionalBook
from vike_trader_app.exec.events import OrderRequest

if TYPE_CHECKING:
    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.live_oms import LiveOmsHub


def _default_clock() -> int:
    import time
    return int(time.time() * 1000)


class LivePortfolioEngine:
    """Multi-symbol live engine interface — the live analogue of ``PortfolioEngine``.

    Parameters
    ----------
    hubs:
        ``{symbol: LiveOmsHub}`` — one hub per symbol; each provides ``submit_ticket``,
        ``venue``, and ``symbol``.
    account:
        The shared ``Account`` fill-model across ALL N symbols.  Positions are keyed
        ``(venue, symbol, "BOTH")``; marks are keyed ``(venue, symbol)``.
    now_ms:
        Clock injection (called on every ``submit``). Defaults to wall-clock ms.
    """

    def __init__(
        self,
        hubs: dict[str, "LiveOmsHub"],
        account: "Account",
        *,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._hubs = hubs
        self._account = account
        self._now_ms = now_ms if now_ms is not None else _default_clock
        # symbols: list[str] — the EXACT attribute PortfolioStrategy / CrossSectionalStrategy reads.
        self.symbols: list[str] = list(hubs)
        # Per-symbol BarSeriesBuffer (NOT shared across symbols).
        self._bufs: dict[str, BarSeriesBuffer] = {
            sym: BarSeriesBuffer([], timeframes=None) for sym in self.symbols
        }
        # Per-symbol last-seen ts (for bars_for / forming_for slicing; mirrors _now in StrategyLiveEngine).
        self._now_by_sym: dict[str, int] = {sym: 0 for sym in self.symbols}
        # Per-symbol ConditionalBook — lazily created on first submit_stop/submit_trailing (A2e).
        self._books: dict[str, ConditionalBook] = {}
        # Monotonic sequence counter for client_order_id uniqueness.
        self._seq: int = 0
        # Stable 8-hex tag for this engine instance — unique across concurrent engines.
        self._engine_tag: str = os.urandom(4).hex()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_coid(self, sym: str) -> str:
        """Return a unique client_order_id: ``<symbol>-<engine-tag>-<seq>``."""
        self._seq += 1
        return f"{sym}-{self._engine_tag}-{self._seq}"

    def _hub(self, sym: str) -> "LiveOmsHub":
        try:
            return self._hubs[sym]
        except KeyError:
            raise ValueError(
                f"symbol {sym!r} is not in the armed basket {self.symbols}"
            ) from None

    # ------------------------------------------------------------------
    # Read-model (the exact _engine.X surface PortfolioStrategy calls)
    # ------------------------------------------------------------------

    @property
    def now(self) -> int:
        """Current time (epoch ms) from the injected clock."""
        return self._now_ms()

    def equity_now(self) -> float:
        """Cash balance + unrealized PnL summed across ALL N symbols.

        Mirrors ``PortfolioEngine``'s equity read:
            equity = cash + sum(position * mark per symbol)
        Here it reads from the shared ``Account`` (fill-accurate, mark-updated).
        """
        total = self._account.balance
        for sym in self.symbols:
            hub = self._hub(sym)
            total += self._account.unrealized_pnl(hub.venue, sym)
        return total

    def position_of(self, sym: str) -> Position:
        """Current position for ``sym`` from the shared Account, as a ``Position``."""
        hub = self._hub(sym)
        raw = self._account.positions.get((hub.venue, sym, "BOTH"), {})
        return Position(size=raw.get("size", 0.0), avg_price=raw.get("avg_px", 0.0))

    def price_of(self, sym: str) -> float:
        """Latest mark price for ``sym`` from the shared Account (0.0 if no mark yet)."""
        hub = self._hub(sym)
        return self._account.marks.get((hub.venue, sym), 0.0)

    # ------------------------------------------------------------------
    # Order verbs (mirror PortfolioEngine's engine-interface surface)
    # ------------------------------------------------------------------

    def submit(
        self,
        sym: str,
        side_sign: int,
        size: float,
        weight: float = 0.0,
        raw: bool = False,
        stop=None,
    ) -> None:
        """Submit a market order for ``sym``.

        ``weight``, ``raw``, and ``stop`` are accepted for signature-parity with the
        backtest ``PortfolioEngine``; they are IGNORED here.  The strategy already
        computed the delta via ``order_target_percent`` / ``buy`` / ``sell``; we route
        the EXPLICIT ``size`` as a plain market order (no sizer, no leverage cap).
        The RiskGate is INSIDE ``hub.submit_ticket`` — do NOT gate here.
        """
        del weight, raw, stop
        if size > 0.0:
            hub = self._hub(sym)
            req = OrderRequest(
                client_order_id=self._next_coid(sym),
                venue=hub.venue,
                symbol=hub.symbol,
                side=side_sign,
                qty=size,
                order_type="market",
                price=None,
                ts=self._now_ms(),
            )
            hub.submit_ticket(req)

    def submit_close(self, sym: str) -> None:
        """Flatten the current position in ``sym`` with a market order (no-op if flat)."""
        hub = self._hub(sym)
        raw = self._account.positions.get((hub.venue, sym, "BOTH"), {})
        held = raw.get("size", 0.0)
        if held == 0.0:
            return
        side = -1 if held > 0.0 else +1
        req = OrderRequest(
            client_order_id=self._next_coid(sym),
            venue=hub.venue,
            symbol=hub.symbol,
            side=side,
            qty=abs(held),
            order_type="market",
            price=None,
            ts=self._now_ms(),
        )
        hub.submit_ticket(req)

    def submit_limit(
        self,
        sym: str,
        side_sign: int,
        size: float,
        price: float,
        weight: float = 0.0,
        raw: bool = False,
        stop=None,
    ) -> None:
        """Submit a resting limit order for ``sym``.

        ``weight``, ``raw``, and ``stop`` are accepted for signature-parity with the backtest
        ``PortfolioEngine.submit_limit`` and are IGNORED here (the strategy already computed
        the desired size; we route it as a plain limit order).
        """
        del weight, raw, stop
        if size > 0.0:
            hub = self._hub(sym)
            req = OrderRequest(
                client_order_id=self._next_coid(sym),
                venue=hub.venue,
                symbol=hub.symbol,
                side=side_sign,
                qty=size,
                order_type="limit",
                price=price,
                ts=self._now_ms(),
            )
            hub.submit_ticket(req)

    def submit_market_close(self, sym: str, side_sign: int, size: float, weight: float = 0.0,
                            raw: bool = False) -> None:
        """Submit a market order to reduce/close the position (explicit direction + size).

        ``weight``/``raw`` are accepted for signature-parity with the backtest ``PortfolioEngine``
        (no sizer/leverage-cap on the live path) and are IGNORED here.
        """
        del weight, raw
        if size > 0.0:
            hub = self._hub(sym)
            req = OrderRequest(
                client_order_id=self._next_coid(sym),
                venue=hub.venue,
                symbol=hub.symbol,
                side=side_sign,
                qty=size,
                order_type="market",
                price=None,
                ts=self._now_ms(),
            )
            hub.submit_ticket(req)

    def submit_limit_close(
        self,
        sym: str,
        side_sign: int,
        size: float,
        price: float,
        weight: float = 0.0,
        raw: bool = False,
    ) -> None:
        """Submit a limit order to reduce/close the position at the given price.

        ``weight``/``raw`` are accepted for signature-parity with the backtest ``PortfolioEngine``
        (no sizer/leverage-cap on the live path) and are IGNORED here.
        """
        del weight, raw
        if size > 0.0:
            hub = self._hub(sym)
            req = OrderRequest(
                client_order_id=self._next_coid(sym),
                venue=hub.venue,
                symbol=hub.symbol,
                side=side_sign,
                qty=size,
                order_type="limit",
                price=price,
                ts=self._now_ms(),
            )
            hub.submit_ticket(req)

    def cancel_all(self, sym: str) -> None:
        """Cancel every resting order for ``sym`` and clear its client-side conditional book (A2e)."""
        hub = self._hub(sym)
        for coid in list(hub.registry):
            hub.cancel_ticket(coid)
        book = self._books.get(sym)
        if book is not None:
            book.clear()

    # ------------------------------------------------------------------
    # Higher-TF reads — delegate to the per-symbol BarSeriesBuffer
    # ------------------------------------------------------------------

    def bars_for(self, sym: str, tf: str):
        """Completed higher-TF bars for ``sym`` visible at the current step (no look-ahead).

        Mirrors ``PortfolioEngine.bars_for(symbol, tf)`` and ``StrategyLiveEngine.bars_for(tf)``;
        delegates to the per-symbol ``BarSeriesBuffer``.
        """
        return self._bufs[sym].bars_for(tf, self._now_by_sym.get(sym, 0))

    def forming_for(self, sym: str, tf: str):
        """The still-forming coarse bar for ``tf`` / ``sym`` from base bars seen so far, or None.

        Mirrors ``PortfolioEngine.forming_for(symbol, tf)``; delegates to the per-symbol
        ``BarSeriesBuffer``.
        """
        return self._bufs[sym].forming_for(tf, self._now_by_sym.get(sym, 0))

    def submit_stop(self, sym: str, side_sign: int, size: float, price: float, weight: float = 0.0) -> None:
        """Register a client-side emulated stop order for ``sym`` (A2e).

        Vike emulates stop orders locally: each closed bar is checked by ``check_conditionals``
        and, when ``bar.high >= price`` (buy-stop) or ``bar.low <= price`` (sell-stop), a plain
        MARKET order is submitted via the existing ``submit`` path (which runs the RiskGate).
        No venue client is contacted with a "stop" order type — every venue adapter only checks
        ``is_limit`` in ``build_order_params``, so a native stop would fire as an immediate
        MARKET with the trigger silently dropped (a real-money mis-order).
        """
        self._books.setdefault(sym, ConditionalBook()).add_stop(side_sign, size, price, weight=weight)

    def submit_trailing(self, sym: str, side_sign: int, size: float, trail: float, weight: float = 0.0) -> None:
        """Register a client-side emulated trailing-stop order for ``sym`` (A2e).

        The trailing extreme is initialised from the symbol's current mark price at registration
        time (mirrors ``BacktestEngine.submit_trailing`` which uses ``extreme=self._price``).
        Each closed bar ratchets the extreme via ``order_fill_price`` and fires a MARKET order
        when the retrace crosses the trigger (``extreme - trail`` for sells, ``extreme + trail``
        for buys).
        """
        extreme = self.price_of(sym)
        self._books.setdefault(sym, ConditionalBook()).add_trailing(
            side_sign, size, trail, extreme=extreme, weight=weight
        )

    def check_conditionals(self, sym: str, bar: Bar) -> list:
        """Fire any triggered conditionals for ``sym`` against this closed bar (A2e).

        Called by ``LivePortfolioPump._try_fire`` BEFORE ``strategy.on_bar`` for each symbol
        in a complete aligned bucket, so fills precede decisions (matching backtest semantics).
        Each fired conditional is submitted as a plain MARKET order through the existing
        ``submit`` path (RiskGate inside that symbol's hub).
        Returns the list of fired ``Order`` objects (fire-once: removed from the book).
        """
        book = self._books.get(sym)
        if book is None:
            return []
        fired = book.check(bar)
        for o in fired:
            self.submit(sym, o.side, o.size)
        return fired

    # ------------------------------------------------------------------
    # Bar buffer — per-symbol BarSeriesBuffer feed
    # ------------------------------------------------------------------

    def add_live_bar(self, sym: str, bar: Bar) -> None:
        """Append a live bar to ``sym``'s buffer and update the Account spot mark.

        The Account mark drives ``price_of`` / ``equity_now`` (the A2c spot-mark fix,
        extended per symbol).  The per-symbol BarSeriesBuffer is separate from other
        symbols' buffers — no cross-symbol contamination.
        """
        self._now_by_sym[sym] = bar.ts
        self._bufs[sym].add_live_bar(bar)
        hub = self._hub(sym)
        self._account.set_mark(hub.venue, sym, bar.close)
