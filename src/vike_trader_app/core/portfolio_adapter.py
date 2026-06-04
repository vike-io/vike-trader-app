"""WealthLab-style portfolio backtest as an adapter over the shared-cash PortfolioEngine.

Runs one copy of a single-symbol ``Strategy`` per symbol; each copy's order calls are forwarded
to one ``PortfolioEngine`` (one cash account, next-open fills, per-symbol PnL). The single-symbol
engine is not touched. Resting orders (limit/stop/trailing) and multi-timeframe reads
(bars_for/forming_for) are forwarded to the shared engine. Multi-timeframe requires
``timeframes=["5m", ...]`` on ``TesterConfig`` (opt-in; omitting it leaves behaviour unchanged).
"""

from .model import Bar
from .portfolio import PortfolioEngine, PortfolioResult, PortfolioStrategy


def align_bars(bars_by_symbol: dict) -> dict:
    """Outer-join every symbol onto the union timeline; forward-fill gaps so all series are equal
    length (PortfolioEngine requires aligned series). A leading gap carries the symbol's first bar
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
    """The slice of ``BacktestEngine`` that a single-symbol ``Strategy`` calls, bound to one symbol
    and forwarding to the shared ``PortfolioEngine``. ``Strategy`` reads ``self._engine.position`` /
    ``equity_now()`` and calls ``submit`` / ``submit_close`` / ``order_target_*``.
    """

    def __init__(self, engine: PortfolioEngine, symbol: str, driver):
        self._engine = engine
        self._symbol = symbol
        self._driver = driver  # holds max_open_positions; may be None in unit tests

    # --- reads ---
    @property
    def position(self):
        return self._engine.position_of(self._symbol)

    def equity_now(self) -> float:
        return self._engine.equity_now()

    def drawdown_now(self) -> float:
        return self._driver.drawdown_now() if self._driver is not None else 0.0

    # --- market orders ---
    def submit(self, side_sign: int, size: float, weight: float = 0.0) -> None:
        if size <= 0:
            return
        self._engine.submit(self._symbol, side_sign, size, weight=weight)

    def submit_close(self) -> None:
        self._engine.submit_close(self._symbol)

    def order_target(self, target: float) -> None:
        delta = target - self._engine.position_of(self._symbol).size
        if abs(delta) > 1e-12:
            self.submit(1 if delta > 0 else -1, abs(delta))

    def order_target_value(self, value: float) -> None:
        price = self._engine.price_of(self._symbol)
        denom = price * self._engine.multiplier
        self.order_target(value / denom if denom else 0.0)

    def order_target_percent(self, pct: float) -> None:
        self.order_target_value(pct * self._engine.equity_now())

    # --- resting orders forwarded to the shared engine ---
    # NOTE: resting orders bypass the MaxOpenPositions cap for now (the cap is checked in
    # submit() for market entries only). This is an accepted v1 limitation — cap-at-fill
    # for resting orders is deferred to W2-C.
    def submit_limit(self, side_sign: int, size: float, price: float, weight: float = 0.0) -> None:
        self._engine.submit_limit(self._symbol, side_sign, size, price, weight=weight)

    def submit_stop(self, side_sign: int, size: float, price: float, weight: float = 0.0) -> None:
        self._engine.submit_stop(self._symbol, side_sign, size, price, weight=weight)

    def submit_trailing(self, side_sign: int, size: float, trail: float, weight: float = 0.0) -> None:
        self._engine.submit_trailing(self._symbol, side_sign, size, trail, weight=weight)

    def cancel_all(self) -> None:
        self._engine.cancel_all(self._symbol)

    # --- multi-timeframe: forward to the shared engine (requires timeframes= on PortfolioEngine) ---
    def bars_for(self, tf: str):
        return self._engine.bars_for(self._symbol, tf)

    def forming_for(self, tf: str):
        return self._engine.forming_for(self._symbol, tf)


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


class MultiSymbolStrategyRunner:
    """Run a single-symbol ``Strategy`` class across every symbol in a DataSet (portfolio backtest)."""

    def __init__(self, strategy_cls, bars_by_symbol: dict, config, max_open_positions: int = 0,
                 ranges: dict | None = None):
        self.strategy_cls = strategy_cls
        self.bars_by_symbol = bars_by_symbol
        self.config = config
        self.max_open_positions = max_open_positions
        # Optional per-symbol membership windows {symbol: [DateRange, ...]} (dynamic DataSet). Falsy
        # (None / {}) leaves behavior identical to a static set — no activity mask is built.
        self.ranges = ranges
        self._engine = None  # the PortfolioEngine built by the most recent run() (probe/diagnostics)

    def run(self) -> PortfolioResult:
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
        engine = PortfolioEngine(aligned, driver,
                                 fee_rate=self.config.fee_rate, cash=self.config.cash,
                                 slippage=self.config.slippage, maker_fee=self.config.maker_fee,
                                 taker_fee=self.config.taker_fee, multiplier=self.config.multiplier,
                                 leverage=self.config.leverage, maint_margin=self.config.maint_margin,
                                 cash_gate=self.config.cash_gate, active_mask=active_mask,
                                 timeframes=self.config.timeframes,
                                 max_open_positions=self.max_open_positions)
        self._engine = engine
        return engine.run()

    def report(self):
        """Run and wrap into a ``TesterReport`` (PortfolioResult is duck-compatible with Result)."""
        from ..tester.report import TesterReport

        return TesterReport.from_result(self.run(), periods_per_year=self.config.periods_per_year)
