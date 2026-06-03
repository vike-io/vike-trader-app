"""WealthLab-style portfolio backtest as an adapter over the shared-cash PortfolioEngine.

Runs one copy of a single-symbol ``Strategy`` per symbol; each copy's order calls are forwarded
to one ``PortfolioEngine`` (one cash account, next-open fills, per-symbol PnL). The single-symbol
engine is not touched. Resting orders (limit/stop/trailing) and multi-timeframe are not supported
in portfolio mode yet — they raise so a strategy that needs them fails loudly rather than silently.
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
    def submit(self, side_sign: int, size: float) -> None:
        if size <= 0:
            return
        opening = self._engine.position_of(self._symbol).size == 0
        if opening and not self._can_open():
            return  # MaxOpenPositions cap reached — skip the entry (WL semantics)
        self._engine.submit(self._symbol, side_sign, size)

    def submit_close(self) -> None:
        self._engine.submit_close(self._symbol)

    def order_target(self, target: float) -> None:
        delta = target - self._engine.position_of(self._symbol).size
        if abs(delta) > 1e-12:
            self.submit(1 if delta > 0 else -1, abs(delta))

    def order_target_value(self, value: float) -> None:
        price = self._engine.price_of(self._symbol)
        self.order_target(value / price if price else 0.0)

    def order_target_percent(self, pct: float) -> None:
        self.order_target_value(pct * self._engine.equity_now())

    # --- unsupported in portfolio mode (fail loudly) ---
    def _unsupported(self, *_a, **_k):
        raise NotImplementedError("resting/multi-timeframe orders are not supported in portfolio mode yet")

    submit_limit = submit_stop = submit_trailing = bars_for = forming_for = _unsupported

    def cancel_all(self) -> None:
        pass  # no resting orders to cancel in portfolio mode

    # --- helpers ---
    def _can_open(self) -> bool:
        cap = getattr(self._driver, "max_open_positions", 0) if self._driver is not None else 0
        if not cap:
            return True
        open_now = sum(1 for s in self._engine.symbols if self._engine.position_of(s).size != 0)
        return open_now < cap
