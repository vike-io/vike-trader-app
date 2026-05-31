"""Multi-symbol screener — rank a universe by a simple indicator rule. Pure functions.

Reads nothing itself: callers pass ``{symbol: closes}`` (the UI loads them from the Catalog).
Each rule maps a trailing close series to ``(signal, value)``; ``screen`` runs a rule across the
universe and returns rows grouped longs-first, so a 36-symbol scan surfaces candidate setups at
the top. No look-ahead — rules read only the trailing window.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

_ORDER = {"long": 0, "short": 1, "neutral": 2}


@dataclass(frozen=True)
class ScreenRow:
    """One screened symbol: its signal, the rule's scalar value, and the last close."""

    symbol: str
    signal: str   # "long" | "short" | "neutral"
    value: float
    last: float


@dataclass(frozen=True)
class ScreenRule:
    """A named screening rule: ``fn(closes) -> (signal, value)``."""

    name: str
    description: str
    fn: object
    long_low: bool = False   # True when a LONG fires on a LOW value (mean-reversion: RSI, Z-score)


def _sma(c, n):
    return sum(c[-n:]) / n if len(c) >= n else None


def _rsi(c, n=14):
    if len(c) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(len(c) - n, len(c)):
        d = c[i] - c[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100.0 - 100.0 / (1 + rs)


def _rule_rsi(low=30.0, high=70.0, n=14):
    def fn(c):
        r = _rsi(c, n)
        if r is None:
            return ("neutral", 0.0)
        return ("long" if r < low else "short" if r > high else "neutral", r)
    return fn


def _rule_sma_trend(n=50):
    def fn(c):
        s = _sma(c, n)
        if s is None or not c:
            return ("neutral", 0.0)
        dev = (c[-1] / s - 1.0) * 100.0
        return ("long" if dev > 0 else "short" if dev < 0 else "neutral", dev)
    return fn


def _rule_roc(n=30):
    def fn(c):
        if len(c) < n + 1 or c[-n - 1] == 0:
            return ("neutral", 0.0)
        roc = (c[-1] / c[-n - 1] - 1.0) * 100.0
        return ("long" if roc > 0 else "short" if roc < 0 else "neutral", roc)
    return fn


def _rule_zscore(n=50, k=2.0):
    def fn(c):
        if len(c) < n:
            return ("neutral", 0.0)
        window = c[-n:]
        mu = sum(window) / n
        sd = statistics.pstdev(window)
        if sd == 0:
            return ("neutral", 0.0)
        z = (c[-1] - mu) / sd
        return ("long" if z <= -k else "short" if z >= k else "neutral", z)
    return fn


RULES: list[ScreenRule] = [
    ScreenRule("RSI(14) 30/70", "Oversold (<30) = long, overbought (>70) = short.",
               _rule_rsi(), long_low=True),
    ScreenRule("SMA(50) trend", "Price above/below the 50-bar SMA (% deviation).", _rule_sma_trend()),
    ScreenRule("ROC(30) momentum", "30-bar rate of change; positive = long.", _rule_roc()),
    ScreenRule("Z-score(50) ±2", "Mean-reversion: z ≤ −2 = long, z ≥ +2 = short.",
               _rule_zscore(), long_low=True),
]


def screen(symbol_closes: dict, rule) -> list[ScreenRow]:
    """Run ``rule`` (a ScreenRule or a bare fn) across ``{symbol: closes}``.

    Rows are grouped long, short, neutral; WITHIN each group they rank by setup STRENGTH
    (distance into the favourable tail), not raw value — so the strongest candidate is on top
    whether the rule is long-on-low (RSI/Z-score) or long-on-high (SMA-trend/ROC).
    """
    fn = getattr(rule, "fn", rule)
    long_low = getattr(rule, "long_low", False)
    rows: list[ScreenRow] = []
    for sym, closes in symbol_closes.items():
        if not closes:
            continue
        signal, value = fn(closes)
        rows.append(ScreenRow(sym, signal, value, closes[-1]))

    def strength(r: ScreenRow) -> float:
        if r.signal == "long":
            return -r.value if long_low else r.value
        if r.signal == "short":
            return r.value if long_low else -r.value
        return 0.0

    rows.sort(key=lambda r: (_ORDER.get(r.signal, 3), -strength(r)))
    return rows
