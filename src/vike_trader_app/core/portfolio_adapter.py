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
