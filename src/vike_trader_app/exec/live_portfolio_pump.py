"""Drive a live strategy (the live analogue of PortfolioEngine.run / BacktestEngine.run).

Unified ``LivePump`` replaces the old split between ``LiveStrategyPump`` (single-symbol) and
the former ``LivePortfolioPump`` (multi-symbol): N=1 is the single-symbol case, N>1 is the
portfolio case.  The same wait-for-all aligner works for both (a 1-symbol bucket is trivially
complete on every arrival — fire-on-each — so behavior is identical to the old single-symbol pump).

Qt-free + single-threaded: ``start()`` / ``feed_bar()`` / ``stop()`` are called only on the Qt
main thread (same thread as EventBus.publish).  A per-symbol ``LiveBarFeedWorker`` (owned by the
UI layer, A2d Task 4) marshals closed bars here via queued signals.  Orders route through
``LiveEngine`` → per-symbol ``LiveOmsHub`` (RiskGate inside each hub).

Strategy types handled:
    1. ``Strategy`` (unified, symbol-explicit ``on_bar(bar)``):
       ``_engine = LiveEngine``; ``_dispatch_step`` fans per-symbol ``on_bar(bar)`` once per
       symbol in the aligned bucket.
    2. ``PortfolioStrategy`` (deprecated bundle ``on_bar(ts, bars)``):
       ``_engine = LiveEngine``; ``_dispatch_step`` calls ``strategy._on_step(ts, bucket)``
       which calls the bundle ``on_bar(ts, bars)`` internally.
    3. ``SingleSymbolStrategy`` (old unkeyed API, N=1 only):
       ``_engine = LiveSymbolShim(engine, symbol)``; ``_dispatch_step`` calls the 1-arg
       ``strategy.on_bar(bar)`` directly (no fan-out; shim translates unkeyed verbs).

Alignment strategy — WAIT-FOR-ALL (user-decided):
    Each incoming bar is placed into a per-timestamp bucket
    ``dict[ts → dict[symbol → Bar]]``.  Once the OLDEST open bucket contains a bar for
    EVERY symbol, it is popped, ``_i`` is advanced, and ``_dispatch_step(ts, bucket)`` is
    fired (warmup-gated), followed by ``schedule.check_due(ts, _i)``.

Late / missing-symbol rule (stale-bucket flush, documented):
    In live feeds, one symbol may fall behind another (exchange outage, WS gap).  A partially
    filled bucket for timestamp T is considered STALE once a strictly-newer timestamp T' > T has
    a COMPLETE bucket ready to fire (all symbols present).  The stale buckets with ts < T' are
    DROPPED (not fired as partial dicts) and a WARNING is logged naming the missing symbols.
    ``_i`` does NOT advance for dropped buckets.

    Rationale:
    - Firing a partial dict (e.g. {"BTC": bar} without ETH) violates the contract every
      ``PortfolioStrategy`` was written for; CrossSectionalStrategy would silently update its
      history for only a subset of symbols.
    - Dropping stale buckets is safer than blocking: missing bars are an exchange-side gap, not
      something the pump can recover; the strategy's warmup history is already in the engine buffer
      (added via ``engine.add_live_bar`` on receipt regardless).
    - There is NO wall-clock timer / maximum-wait limit — the rule is purely order-based
      (triggered when a newer ts fully completes), so the pump stays deterministic and testable
      without sleep or threads.

    If you need a time-based timeout instead, add it in the UI layer (stop the strategy if no
    bar arrives for a symbol within N seconds) — that policy does not belong in the pump.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from vike_trader_app.core.compat_strategy import SingleSymbolStrategy
from vike_trader_app.exec.live_portfolio_engine import LiveEngine
from vike_trader_app.exec.live_symbol_shim import LiveSymbolShim
from vike_trader_app.exec.strategy_event_adapter import StrategyEventAdapter

if TYPE_CHECKING:
    from vike_trader_app.core.model import Bar

log = logging.getLogger(__name__)


class LivePump:
    """Unified wait-for-all bar aligner + lifecycle driver for all Strategy types.

    Parameters
    ----------
    strategy:
        A ``Strategy``, ``PortfolioStrategy``, or ``SingleSymbolStrategy`` (deprecated).
        The pump sets ``strategy._engine`` on construction (a ``LiveEngine`` for the unified /
        portfolio case; a ``LiveSymbolShim`` for the SingleSymbolStrategy N=1 case).
        ``on_start`` / ``on_stop`` are called if defined (safe with getattr).
    hubs:
        ``{symbol: LiveOmsHub}`` — forwarded verbatim to ``LiveEngine``.
    account:
        The shared ``Account`` across all N symbols.
    now_ms:
        Clock injection for ``LiveEngine``; defaults to wall-clock ms.
    timeframes:
        Optional list of higher timeframes forwarded to the per-symbol ``BarSeriesBuffer``.
    """

    def __init__(
        self,
        strategy,
        hubs: dict,
        account,
        *,
        now_ms: Callable[[], int] | None = None,
        timeframes=None,
    ) -> None:
        self.strategy = strategy
        self.engine = LiveEngine(hubs, account, now_ms=now_ms, timeframes=timeframes)

        # --- Strategy binding ---
        # SingleSymbolStrategy + N=1: wire a LiveSymbolShim (unkeyed API compat).
        # All other strategies: wire the LiveEngine directly.
        symbols_list = list(hubs)
        if isinstance(strategy, SingleSymbolStrategy) and len(hubs) == 1:
            self._single_symbol: str | None = symbols_list[0]
            strategy._engine = LiveSymbolShim(self.engine, self._single_symbol)
        else:
            self._single_symbol = None
            strategy._engine = self.engine

        # --- Per-hub StrategyEventAdapter (subscribed AFTER hubs, mirroring LiveStrategyPump) ---
        # Each hub's EventBus gets one adapter so the strategy's A1 handlers (on_order_filled,
        # on_position_opened, …) fire for venue events on that symbol's bus.
        self._adapters: list[StrategyEventAdapter] = [
            StrategyEventAdapter(strategy, h.bus) for h in hubs.values()
        ]

        # _i: aligned-bar counter.  Starts at -1; advances by 1 per fired on_bar.
        # strategy.index mirrors _i AFTER each advance (on_bar gate uses _i, not strategy.index).
        self._i: int = -1
        self._started: bool = False

        # Per-timestamp alignment buckets: ts → {symbol → Bar}
        # Sorted insertion order is preserved (Python 3.7+ dict keeps insertion order;
        # ts values from a live feed are monotonically increasing in the common case).
        self._buckets: dict[int, dict[str, "Bar"]] = {}

        self._n_symbols: int = len(self.engine.symbols)
        self._symbols: set[str] = set(self.engine.symbols)

        # Monotonic watermark: the ts of the last on_bar that fired.
        # Replaces the unbounded ``_fired_ts`` set (O(1) per bar vs O(N) set).
        # Dual purpose:
        #   1. Replay/dup guard: a bar whose ts <= _last_fired_ts was already aligned+fired;
        #      discard it (equivalent to the old `if ts in _fired_ts: return`).
        #   2. Time-regression guard: a late completion of an OLD ts (after a newer ts already
        #      fired) cannot re-enter _try_fire at all — feed_bar drops it before it reaches
        #      the bucket.
        self._last_fired_ts: int = -1

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def prime(self, history_by_symbol: "dict[str, list[Bar]]") -> None:
        """Warm the engine buffers + advance the bar index WITHOUT firing ``on_bar``.

        Call this BEFORE ``start()`` and before the live workers begin feeding bars.  Each
        symbol's history bars are pushed through ``engine.add_live_bar`` (populating the per-
        symbol ``BarSeriesBuffer`` and setting the Account mark), and ``_i`` / ``strategy.index``
        are advanced by the number of ALIGNED timestamps — ``min(len(bars) for sym in symbols)``
        — matching the wait-for-all alignment contract (``_i`` counts aligned steps, not raw
        per-symbol bars).

        After priming with ≥ WARMUP aligned bars, the very first live aligned ``on_bar`` fires
        immediately (warmup gate already satisfied).  This mirrors ``LiveStrategyPump.prime``
        extended to N symbols.

        NOTE: ``on_start`` is NOT called here; call ``start()`` separately AFTER priming.
        ``on_bar`` is intentionally NOT fired for primed bars — they are purely history.
        """
        symbols = self.engine.symbols
        # Feed ALL history bars for each symbol into the engine buffer (mark + BarSeriesBuffer).
        for sym in symbols:
            bars = history_by_symbol.get(sym, [])
            for bar in bars:
                self.engine.add_live_bar(sym, bar)

        # Advance _i / strategy.index by the number of ALIGNED (wait-for-all) steps.
        # Each aligned step = one ts for which ALL symbols have a bar.  The number of such
        # steps is min(len(bars)) over all symbols (mirrors the wait-for-all contract).
        if symbols:
            aligned_steps = min(
                len(history_by_symbol.get(sym, [])) for sym in symbols
            )
            if aligned_steps > 0:
                self._i += aligned_steps
                self.strategy.index = self._i

    def start(self) -> None:
        """Arm the pump: call ``strategy.on_start()`` (if defined) and open the gate."""
        if self._started:
            return
        cb = getattr(self.strategy, "on_start", None)
        if cb is not None:
            try:
                cb()
            except Exception:  # noqa: BLE001
                log.exception("strategy.on_start raised; pump continues")
        self._started = True

    def stop(self) -> None:
        """Disarm the pump: call ``strategy.on_stop()`` (if defined), unsubscribe adapters."""
        if not self._started:
            return
        self._started = False
        cb = getattr(self.strategy, "on_stop", None)
        if cb is not None:
            try:
                cb()
            except Exception:  # noqa: BLE001
                log.exception("strategy.on_stop raised")
        for adapter in self._adapters:
            adapter.unsubscribe()

    # ------------------------------------------------------------------
    # Bar ingestion
    # ------------------------------------------------------------------

    def feed_bar(self, symbol: str, bar: "Bar") -> None:
        """Accept one closed bar for ``symbol`` (MAIN THREAD ONLY).

        1. Guard: drop late bars arriving after ``stop()``.
        2. Forward bar to the engine buffer (mark update + BarSeriesBuffer).
        3. Add bar to the per-timestamp alignment bucket.
        4. Flush any stale incomplete buckets whose ts is older than the oldest
           complete bucket (see module docstring for the stale-bucket rule).
        5. If the OLDEST open bucket is now complete → pop + fire ``on_bar``.
        """
        if not self._started:
            return  # A: guard late queued bars arriving after stop()

        # B: update the engine buffer + set the account mark for this symbol.
        self.engine.add_live_bar(symbol, bar)

        # C: slot into the per-timestamp bucket (skip if already fired — replay/regression guard).
        ts = bar.ts
        if ts <= self._last_fired_ts:
            return  # already aligned+fired (replay) OR older than last fired (time regression)
        bucket = self._buckets.setdefault(ts, {})
        bucket[symbol] = bar  # last-writer-wins if same symbol arrives twice for same ts

        # D: check if any bucket is now complete and fire/flush as needed.
        self._try_fire()

    # ------------------------------------------------------------------
    # Internal: dispatch (polymorphic for all three strategy types)
    # ------------------------------------------------------------------

    def _dispatch_step(self, ts: int, bucket: "dict[str, Bar]") -> None:
        """Call the correct ``on_bar`` variant for the strategy type.

        - ``SingleSymbolStrategy`` (shim path, N=1): ``strategy.on_bar(bar)`` — 1-arg.
        - ``Strategy`` (unified): ``strategy._on_step(ts, bucket)`` which fans
          ``on_bar(bar)`` once per symbol.
        - ``PortfolioStrategy`` (deprecated bundle): ``strategy._on_step(ts, bucket)``
          which calls the bundle ``on_bar(ts, bars)``.
        Both non-shim cases go through ``_on_step`` — polymorphic dispatch is already
        implemented there (Strategy fans per-symbol; PortfolioStrategy bundles).
        """
        if self._single_symbol is not None:
            # SingleSymbolStrategy via LiveSymbolShim: call the old 1-arg on_bar.
            self.strategy.on_bar(bucket[self._single_symbol])
        else:
            # Strategy (unified) or PortfolioStrategy (bundle) — both use _on_step.
            self.strategy._on_step(ts, bucket)

    # ------------------------------------------------------------------
    # Internal: align and fire
    # ------------------------------------------------------------------

    def _try_fire(self) -> None:
        """Scan buckets in ts order; flush stale ones; fire the first complete one."""
        if not self._buckets:
            return

        # Walk ts in ascending order (dict insertion order is ts-ordered for normal feeds;
        # sort for safety in case of out-of-order arrival).
        sorted_ts = sorted(self._buckets)

        # Find the OLDEST complete bucket.
        complete_ts: int | None = None
        for ts in sorted_ts:
            if len(self._buckets[ts]) >= self._n_symbols:
                complete_ts = ts
                break

        if complete_ts is None:
            return  # nothing complete yet — wait

        # Drop all stale INCOMPLETE buckets that are STRICTLY OLDER than complete_ts.
        stale = [ts for ts in sorted_ts if ts < complete_ts]
        for ts in stale:
            missing = self._symbols - set(self._buckets[ts].keys())
            log.warning(
                "LivePump: dropping stale incomplete bucket ts=%d "
                "(missing symbols: %s); newer ts=%d is complete.",
                ts,
                sorted(missing),
                complete_ts,
            )
            del self._buckets[ts]

        # Pop and fire the complete bucket.
        fired_bucket = self._buckets.pop(complete_ts)
        self._last_fired_ts = complete_ts  # advance monotonic watermark (replay + regression guard)
        self._i += 1
        self.strategy.index = self._i

        # Check conditionals for each symbol BEFORE on_bar (fills precede decisions,
        # matching backtest semantics).  Conditionals fire regardless of the warmup gate
        # — they were armed by the strategy and must trigger on the crossing bar.
        for sym, bar in fired_bucket.items():
            self.engine.check_conditionals(sym, bar)

        warmup = getattr(self.strategy, "WARMUP", 0)
        if self._i >= warmup:
            try:
                self._dispatch_step(complete_ts, fired_bucket)
            except Exception:  # noqa: BLE001
                log.exception(
                    "LivePump: strategy dispatch raised at ts=%d; "
                    "pump continues (disarm to stop)",
                    complete_ts,
                )

            # Fire schedule AFTER on_bar (mirror portfolio.py:560-563).
            sched = getattr(self.strategy, "schedule", None)
            if sched is not None:
                try:
                    for cb in sched.check_due(complete_ts, self._i):
                        cb()
                except Exception:  # noqa: BLE001
                    log.exception(
                        "LivePump: schedule callback raised at ts=%d; "
                        "pump continues",
                        complete_ts,
                    )


# ---------------------------------------------------------------------------
# Backward-compatibility alias — existing imports/tests reference LivePortfolioPump.
# ---------------------------------------------------------------------------
LivePortfolioPump = LivePump
