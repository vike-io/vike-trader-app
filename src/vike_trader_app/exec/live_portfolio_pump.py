"""Drive a multi-symbol ``PortfolioStrategy`` live (the live analogue of PortfolioEngine.run).

Qt-free + single-threaded: ``start()`` / ``feed_bar()`` / ``stop()`` are called only on the Qt
main thread (same thread as EventBus.publish).  A per-symbol ``LiveBarFeedWorker`` (owned by the
UI layer, A2d Task 4) marshals closed bars here via queued signals.  Orders route through
``LivePortfolioEngine`` → per-symbol ``LiveOmsHub`` (RiskGate inside each hub).

Alignment strategy — WAIT-FOR-ALL (user-decided):
    Each incoming bar is placed into a per-timestamp bucket
    ``dict[ts → dict[symbol → Bar]]``.  Once the OLDEST open bucket contains a bar for
    EVERY symbol, it is popped, ``_i`` is advanced, and ``strategy.on_bar(ts, bucket)`` is
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

from vike_trader_app.exec.live_portfolio_engine import LivePortfolioEngine

if TYPE_CHECKING:
    from vike_trader_app.core.model import Bar

log = logging.getLogger(__name__)


class LivePortfolioPump:
    """Wait-for-all multi-symbol bar aligner + lifecycle driver for ``PortfolioStrategy``.

    Parameters
    ----------
    strategy:
        A ``PortfolioStrategy`` (or subclass).  The pump sets ``strategy._engine`` on
        construction; ``on_start`` / ``on_stop`` are called if defined (safe with getattr).
    hubs:
        ``{symbol: LiveOmsHub}`` — forwarded verbatim to ``LivePortfolioEngine``.
    account:
        The shared ``Account`` across all N symbols.
    now_ms:
        Clock injection for ``LivePortfolioEngine``; defaults to wall-clock ms.
    """

    def __init__(
        self,
        strategy,
        hubs: dict,
        account,
        *,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.strategy = strategy
        self.engine = LivePortfolioEngine(hubs, account, now_ms=now_ms)
        strategy._engine = self.engine

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

        # Set of ts values already fired — prevents re-firing if the same ts is re-fed
        # (e.g., a WS reconnect replaying a bar that was already aligned and sent to on_bar).
        self._fired_ts: set[int] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

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
        """Disarm the pump: call ``strategy.on_stop()`` (if defined) and close the gate."""
        if not self._started:
            return
        self._started = False
        cb = getattr(self.strategy, "on_stop", None)
        if cb is not None:
            try:
                cb()
            except Exception:  # noqa: BLE001
                log.exception("strategy.on_stop raised")

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

        # C: slot into the per-timestamp bucket (skip if already fired for this ts).
        ts = bar.ts
        if ts in self._fired_ts:
            return  # already aligned + fired; discard the replay bar
        bucket = self._buckets.setdefault(ts, {})
        bucket[symbol] = bar  # last-writer-wins if same symbol arrives twice for same ts

        # D: check if any bucket is now complete and fire/flush as needed.
        self._try_fire()

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
                "LivePortfolioPump: dropping stale incomplete bucket ts=%d "
                "(missing symbols: %s); newer ts=%d is complete.",
                ts,
                sorted(missing),
                complete_ts,
            )
            del self._buckets[ts]

        # Pop and fire the complete bucket.
        fired_bucket = self._buckets.pop(complete_ts)
        self._fired_ts.add(complete_ts)  # guard against replay of already-fired ts
        self._i += 1
        self.strategy.index = self._i

        warmup = getattr(self.strategy, "WARMUP", 0)
        if self._i >= warmup:
            try:
                self.strategy.on_bar(complete_ts, fired_bucket)
            except NotImplementedError:
                log.warning(
                    "LivePortfolioPump: strategy used a not-yet-supported order type "
                    "(stop/trailing → A2e); skipped for ts=%d",
                    complete_ts,
                )
            except Exception:  # noqa: BLE001
                log.exception(
                    "LivePortfolioPump: strategy.on_bar raised at ts=%d; "
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
                        "LivePortfolioPump: schedule callback raised at ts=%d; "
                        "pump continues",
                        complete_ts,
                    )
