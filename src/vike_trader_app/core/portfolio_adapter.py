"""WealthLab-style portfolio backtest as an adapter over the shared-cash MultiSymbolEngine.

Runs one copy of a single-symbol ``Strategy`` per symbol; each copy's order calls are forwarded
to one ``MultiSymbolEngine`` (one cash account, next-open fills, per-symbol PnL). The single-symbol
engine is not touched. Resting orders (limit/stop/trailing) and multi-timeframe reads
(bars_for/forming_for) are forwarded to the shared engine. Multi-timeframe requires
``timeframes=["5m", ...]`` on ``TesterConfig`` (opt-in; omitting it leaves behaviour unchanged).
"""

import bisect

from .model import Bar
from .multi_symbol_engine import MultiSymbolEngine, MultiSymbolResult, PortfolioStrategy


def _buyhold_asof(benchmark_bars: list, equity_ts: list, cash: float) -> list:
    """As-of-aligned buy-&-hold curve for a benchmark symbol.

    For each timestamp in *equity_ts* we find the last benchmark bar whose ``ts``
    is <= that timestamp (forward-fill; no look-ahead). Before the first benchmark
    bar the ratio is 1.0 (i.e. the benchmark hasn't started yet → value stays at
    *cash*). Returns a list of floats with ``len(equity_ts)`` entries.
    """
    ts_list = [b.ts for b in benchmark_bars]
    first_close = benchmark_bars[0].close
    result: list[float] = []
    for t in equity_ts:
        # bisect_right gives the insertion point after all ts_list entries <= t;
        # subtracting 1 gives the index of the last bar with ts <= t.
        idx = bisect.bisect_right(ts_list, t) - 1
        if idx < 0:
            # Before the first benchmark bar — use ratio 1.0 (no movement yet)
            close = first_close
        else:
            close = benchmark_bars[idx].close
        result.append(cash * (close / first_close))
    return result


def align_bars(bars_by_symbol: dict) -> dict:
    """Outer-join every symbol onto the union timeline; forward-fill gaps so all series are equal
    length (MultiSymbolEngine requires aligned series). A leading gap carries the symbol's first bar
    (flat); an interior/trailing gap carries the last seen close as a zero-volume bar.
    """
    timeline = sorted({bar.ts for series in bars_by_symbol.values() for bar in series})
    out: dict = {}
    for sym, series in bars_by_symbol.items():
        by_ts = {bar.ts: bar for bar in series}
        first = series[0] if series else None
        filled, last = [], first
        for ts in timeline:
            if ts in by_ts:
                last = by_ts[ts]
                filled.append(last)
            elif last is not None:
                c = last.close
                filled.append(Bar(ts=ts, open=c, high=c, low=c, close=c, volume=0.0))
        out[sym] = filled
    return out


class SymbolEngineShim:
    """The slice of ``SingleSymbolEngine`` that a single-symbol ``Strategy`` calls, bound to one symbol
    and forwarding to the shared ``MultiSymbolEngine``. ``Strategy`` reads ``self._engine.position`` /
    ``equity_now()`` and calls ``submit`` / ``submit_close`` / ``order_target_*``.
    """

    def __init__(self, engine: MultiSymbolEngine, symbol: str, driver):
        self._engine = engine
        self._symbol = symbol
        self._driver = driver  # holds max_open_positions; may be None in unit tests

    # --- reads ---
    @property
    def position(self):
        return self._engine.position_of(self._symbol)

    def position_of(self, symbol: str):  # new Strategy API compat — symbol ignored (shim is per-symbol)
        return self._engine.position_of(self._symbol)

    def price_of(self, symbol: str) -> float:  # new Strategy API compat
        return self._engine.price_of(self._symbol)

    @property
    def now(self) -> int:
        return self._engine.now

    def equity_now(self) -> float:
        return self._engine.equity_now()

    def drawdown_now(self) -> float:
        return self._driver.drawdown_now() if self._driver is not None else 0.0

    @property
    def symbols(self) -> list:
        return [self._symbol]

    def _pending_of(self, symbol: str) -> list:  # new Strategy API compat
        return self._engine._pending_of(self._symbol)

    # --- market orders ---
    def submit(self, side_sign_or_symbol, side_or_size=None, size=None,
               weight: float = 0.0, raw: bool = False, stop=None) -> None:
        # Compat: old API submit(side, size) / new API submit(symbol, side, size).
        if isinstance(side_sign_or_symbol, str):
            # new-API call from unified Strategy: submit(symbol, side, size, ...)
            side_sign, sz = side_or_size, size
        else:
            # old-API call from SingleSymbolStrategy: submit(side, size, ...)
            side_sign = side_sign_or_symbol
            sz = side_or_size if size is None else size
        if sz is None or sz <= 0:
            return
        self._engine.submit(self._symbol, side_sign, sz, weight=weight, raw=raw, stop=stop)

    def submit_close(self, symbol: str | None = None) -> None:  # symbol ignored
        self._engine.submit_close(self._symbol)

    def order_target(self, target: float) -> None:
        delta = target - self._engine.position_of(self._symbol).size
        if abs(delta) > 1e-12:
            self.submit(1 if delta > 0 else -1, abs(delta), raw=True)  # explicit qty: do not re-size

    def order_target_value(self, value: float) -> None:
        price = self._engine.price_of(self._symbol)
        denom = price * self._engine.multiplier
        self.order_target(value / denom if denom else 0.0)

    def order_target_percent(self, pct: float) -> None:
        self.order_target_value(pct * self._engine.equity_now())

    # --- resting orders forwarded to the shared engine ---
    # Resting orders (limit/stop/trailing) ARE subject to the MaxOpenPositions + per-direction caps:
    # the cap is checked at FILL time in MultiSymbolEngine._fill_pending / _fill_pending_granular
    # (a new-symbol open is dropped when _at_open_cap()), not just for market entries in submit().
    # Covered by test_max_open_positions_caps_resting_entries.
    def submit_limit(self, side_sign_or_symbol, side_or_size=None, size_or_price=None,
                     price=None, weight: float = 0.0, stop=None) -> None:
        # Compat: old submit_limit(side, size, price) / new submit_limit(sym, side, size, price)
        if isinstance(side_sign_or_symbol, str):
            side_sign, size, price = side_or_size, size_or_price, price
        else:
            side_sign, size, price = side_sign_or_symbol, side_or_size, size_or_price
        self._engine.submit_limit(self._symbol, side_sign, size, price, weight=weight, stop=stop)

    def submit_stop(self, side_sign_or_symbol, side_or_size=None, size_or_price=None,
                    price=None, weight: float = 0.0) -> None:
        # Compat: old submit_stop(side, size, price) / new submit_stop(sym, side, size, price)
        if isinstance(side_sign_or_symbol, str):
            side_sign, size, price = side_or_size, size_or_price, price
        else:
            side_sign, size, price = side_sign_or_symbol, side_or_size, size_or_price
        self._engine.submit_stop(self._symbol, side_sign, size, price, weight=weight)

    def submit_trailing(self, side_sign: int, size: float, trail: float, weight: float = 0.0) -> None:
        self._engine.submit_trailing(self._symbol, side_sign, size, trail, weight=weight)

    def submit_market_close(self, side_sign: int, size: float, weight: float = 0.0) -> None:
        self._engine.submit_market_close(self._symbol, side_sign, size, weight=weight)

    def submit_limit_close(self, side_sign: int, size: float, price: float, weight: float = 0.0) -> None:
        self._engine.submit_limit_close(self._symbol, side_sign, size, price, weight=weight)

    def cancel_order(self, symbol, order) -> None:  # noqa: ARG002
        """Cancel a specific resting order via the shared MultiSymbolEngine for this shim's symbol."""
        self._engine.cancel_order(self._symbol, order)

    def cancel_all(self, symbol: str | None = None) -> None:  # symbol ignored
        self._engine.cancel_all(self._symbol)

    # --- multi-timeframe: forward to the shared engine (requires timeframes= on MultiSymbolEngine) ---
    def bars_for(self, symbol_or_tf, tf: str | None = None):
        # Compat: old bars_for(tf) / new bars_for(symbol, tf)
        _tf = tf if tf is not None else symbol_or_tf
        return self._engine.bars_for(self._symbol, _tf)

    def forming_for(self, symbol_or_tf, tf: str | None = None):
        # Compat: old forming_for(tf) / new forming_for(symbol, tf)
        _tf = tf if tf is not None else symbol_or_tf
        return self._engine.forming_for(self._symbol, _tf)


class _MultiSymbolDriver(PortfolioStrategy):
    """A PortfolioStrategy that fans each step out to one single-symbol Strategy per symbol."""

    def __init__(self, strategy_cls, symbols, max_open_positions: int = 0):
        super().__init__()
        self._cls = strategy_cls
        self._symbols = list(symbols)
        self.max_open_positions = max_open_positions
        self._inner: dict = {}
        self._peak = None  # equity peak for drawdown_now()

    def _ensure_init(self) -> None:
        if self._inner:
            return
        for sym in self._symbols:
            strat = self._cls()
            strat._engine = SymbolEngineShim(self._engine, sym, self)
            self._inner[sym] = strat

    def drawdown_now(self) -> float:
        eq = self._engine.equity_now()
        self._peak = eq if self._peak is None else max(self._peak, eq)
        return 0.0 if not self._peak else max(0.0, 1.0 - eq / self._peak)

    def on_bar(self, ts: int, bars: dict) -> None:
        self._ensure_init()
        for sym, bar in bars.items():
            if not self._engine.is_active(sym):  # inactive member this bar: don't run its strategy
                continue
            inner = self._inner[sym]
            if self.index < getattr(inner, "WARMUP", 0):  # honor each strategy's warmup
                continue
            inner.index = self.index
            inner.on_bar(bar)
            sched = getattr(inner, "schedule", None)
            if sched is not None:
                for _cb in sched.check_due(ts, self.index):
                    _cb()


class MultiSymbolStrategyRunner:
    """Run a single-symbol ``Strategy`` class across every symbol in a DataSet (portfolio backtest)."""

    def __init__(self, strategy_cls, bars_by_symbol: dict, config, max_open_positions: int = 0,
                 ranges: dict | None = None, granular_by_symbol: dict | None = None,
                 benchmark_bars: list | None = None, benchmark_label: str = ""):
        self.strategy_cls = strategy_cls
        self.bars_by_symbol = bars_by_symbol
        self.config = config
        self.max_open_positions = max_open_positions
        # Optional per-symbol membership windows {symbol: [DateRange, ...]} (dynamic DataSet). Falsy
        # (None / {}) leaves behavior identical to a static set — no activity mask is built.
        self.ranges = ranges
        # Optional per-symbol finer (e.g. 1m) bars {symbol: [Bar, ...]} for granular intraday fill
        # processing (WL "Use Granular Limit/Stop Processing"). Sub-bars are keyed by ts ranges in the
        # engine, so they need NO alignment. Falsy (None) leaves behavior unchanged (coarse path).
        self.granular_by_symbol = granular_by_symbol
        # Optional benchmark bars for a specific symbol; overrides the equal-weight default when set.
        self.benchmark_bars = benchmark_bars
        self.benchmark_label = benchmark_label
        self._engine = None  # the MultiSymbolEngine built by the most recent run() (probe/diagnostics)

    def run(self) -> MultiSymbolResult:
        aligned = align_bars(self.bars_by_symbol)
        driver = _MultiSymbolDriver(self.strategy_cls, list(aligned), self.max_open_positions)
        active_mask = None
        if self.ranges:
            active_mask = {}
            for s in aligned:
                windows = self.ranges.get(s)
                if not windows:
                    active_mask[s] = [True] * len(aligned[s])
                else:
                    active_mask[s] = [any(w.contains(b.ts) for w in windows) for b in aligned[s]]
        engine = MultiSymbolEngine(
            aligned, driver,
            active_mask=active_mask,
            max_open_positions=self.max_open_positions,
            granular_by_symbol=self.granular_by_symbol,
            **self.config.portfolio_engine_kwargs(),
        )
        self._engine = engine
        result = engine.run()
        # --- equal-weight buy-&-hold benchmark curve ---
        # For each bar index i, bench[i] = cash * mean(close_s[i] / close_s[0]) over usable symbols
        # (those with a positive first close). Aligned bars and equity_curve share the same timeline.
        cash = self.config.cash
        usable = []
        for sym, bars in aligned.items():
            first_close = bars[0].close if bars else 0.0
            if first_close > 0:
                usable.append((sym, bars, first_close))
        if usable and len(result.equity_curve) == len(next(iter(aligned.values()))):
            n_bars = len(result.equity_curve)
            bench: list[float] = []
            for i in range(n_bars):
                ratios = [bars[i].close / first_close for _, bars, first_close in usable]
                mean_ratio = sum(ratios) / len(ratios)
                bench.append(cash * mean_ratio)
            result.benchmark_curve = bench
            result.benchmark_label = "Equal-weight buy & hold"
        # --- override with specific benchmark symbol if provided ---
        if (self.benchmark_bars
                and len(self.benchmark_bars) > 0
                and self.benchmark_bars[0].close > 0
                and result.equity_ts):
            result.benchmark_curve = _buyhold_asof(self.benchmark_bars, result.equity_ts, cash)
            result.benchmark_label = self.benchmark_label or "Benchmark"
        return result

    def report(self):
        """Run and wrap into a ``TesterReport`` (MultiSymbolResult is duck-compatible with Result)."""
        from ..tester.report import TesterReport

        return TesterReport.from_result(self.run(), periods_per_year=self.config.periods_per_year)
