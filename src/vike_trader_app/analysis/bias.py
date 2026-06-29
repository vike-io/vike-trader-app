"""Look-ahead bias detection (the "won't let you fool yourself" guard).

A strategy that peeks at future data makes different decisions when that future is
truncated away. We record the orders a strategy submits at a probe bar (1) given the
full data view and (2) given the data truncated right after the probe; if they differ,
the strategy used information it could not have had at decision time.

``make(view)`` must build a fresh Strategy that may only use ``view`` (the bars it is
allowed to see). Honest strategies ignore ``view`` and read only ``on_bar``'s bar.
"""

from ..core.single_symbol_engine import SingleSymbolEngine

_ORDER_METHODS = ("buy", "sell", "close", "limit_buy", "limit_sell", "stop_buy", "stop_sell")


def _orders_at(make, bars, probe: int):
    """Return the list of orders a fresh strategy submits at bar index ``probe``."""
    strat = make(bars)
    log: list[tuple] = []

    def _wrap(name):
        original = getattr(strat, name)

        def recorder(*args, **kwargs):
            log.append((strat.index, name, args, tuple(sorted(kwargs.items()))))
            return original(*args, **kwargs)

        return recorder

    for name in _ORDER_METHODS:
        setattr(strat, name, _wrap(name))

    SingleSymbolEngine(bars, strat).run()
    return [entry[1:] for entry in log if entry[0] == probe]


def detect_lookahead(make, bars, probe: int) -> bool:
    """True if the strategy's decision at ``probe`` changes when the future is truncated."""
    full = _orders_at(make, bars, probe)
    truncated = _orders_at(make, bars[: probe + 1], probe)
    return full != truncated


def scan_lookahead(make, bars, probes=None) -> list[int]:
    """Return the probe indices where look-ahead is detected (empty == clean)."""
    if probes is None:
        n = len(bars)
        probes = [i for i in range(1, n - 1)]  # skip first/last (no future to truncate)
    return [p for p in probes if detect_lookahead(make, bars, p)]
