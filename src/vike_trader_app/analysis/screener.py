"""Multi-symbol screener — rank a universe by a simple indicator rule. Pure functions.

Reads nothing itself: callers pass ``{symbol: closes}`` (the UI loads them from the Catalog).
Each rule maps a trailing close series to ``(signal, value)``; ``screen`` runs a rule across the
universe and returns rows grouped longs-first, so a 36-symbol scan surfaces candidate setups at
the top. No look-ahead — rules read only the trailing window.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path

from ..data import state_db

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


# --- composite (multi-condition) rules ------------------------------------------------

@dataclass(frozen=True)
class Condition:
    """One leg of a composite: a base ``ScreenRule.name`` and the required signal direction."""

    rule: str                 # a base ScreenRule.name
    direction: str = "long"   # required signal: "long" | "short" | "neutral"


@dataclass(frozen=True)
class CompositeRule:
    """An AND/OR combination of base-rule conditions.

    ``__call__(closes) -> (signal, value)`` where ``value`` is the count of satisfied
    conditions. Fires (emits ``direction``) when ALL conditions hold (``combine='AND'``)
    or ANY holds (``combine='OR'``); otherwise emits ``"neutral"``. It deliberately has no
    ``.fn`` attribute, so ``screen`` falls back to calling the object directly.
    """

    name: str
    description: str
    conditions: tuple = ()
    combine: str = "AND"
    direction: str = "long"
    long_low: bool = False

    def __call__(self, closes):
        satisfied = 0
        for c in self.conditions:
            base = rule_by_name(c.rule)
            if base is None:
                continue
            sig, _ = base.fn(closes)
            if sig == c.direction:
                satisfied += 1
        if self.combine.upper() == "AND":
            fired = satisfied == len(self.conditions) and len(self.conditions) > 0
        else:
            fired = satisfied > 0
        if fired:
            return (self.direction, float(satisfied))
        return ("neutral", float(satisfied))


_COMPOSITES: dict[str, CompositeRule] = {}


def register_composite(rule: CompositeRule) -> None:
    """Add (or replace) ``rule`` in the live composite registry, keyed by name."""
    _COMPOSITES[rule.name] = rule


def composites() -> list[CompositeRule]:
    """The currently registered composite rules."""
    return list(_COMPOSITES.values())


def rule_by_name(name: str):
    """Resolve ``name`` to a base ``ScreenRule`` first, then a registered ``CompositeRule``.

    Returns ``None`` when the name is unknown.
    """
    for r in RULES:
        if r.name == name:
            return r
    return _COMPOSITES.get(name)


def composite_to_dict(rule: CompositeRule) -> dict:
    """Serialise a CompositeRule to a JSON-friendly dict."""
    return {
        "name": rule.name,
        "description": rule.description,
        "conditions": [{"rule": c.rule, "direction": c.direction} for c in rule.conditions],
        "combine": rule.combine,
        "direction": rule.direction,
        "long_low": rule.long_low,
    }


def composite_from_dict(d: dict) -> CompositeRule:
    """Rebuild a CompositeRule from ``composite_to_dict`` output (inverse)."""
    conditions = tuple(
        Condition(rule=c["rule"], direction=c.get("direction", "long"))
        for c in d.get("conditions", [])
    )
    return CompositeRule(
        name=d["name"],
        description=d.get("description", ""),
        conditions=conditions,
        combine=d.get("combine", "AND"),
        direction=d.get("direction", "long"),
        long_low=d.get("long_low", False),
    )


_COMPOSITES_TABLE = "composites"


class CompositeStore:
    """The CompositeRule list in the app DB (table ``composites``); loading registers each into
    the live registry.

    The list is one row — the whole list as a single JSON payload, read and written whole
    exactly like the legacy file (see :mod:`vike_trader_app.data.state_db`). ``path`` is the
    *legacy* JSON file — read only by the one-time sweep (kept as the first positional argument
    so existing callers don't change); the DB lives beside it. ``db_path`` is the explicit test
    seam. Public API is unchanged from the JSON-file store.
    """

    def __init__(self, path: str = "storage/composites.json", *,
                 db_path: str | Path | None = None):
        self.path = Path(path)
        self.db = Path(db_path) if db_path is not None else state_db.db_for_file(path)
        self._rules: list[CompositeRule] = []
        self.load()

    def load(self) -> list[CompositeRule]:
        payload = state_db.load_blob(_COMPOSITES_TABLE, self.path, db_path=self.db)
        try:
            self._rules = [composite_from_dict(d) for d in payload or []]
        except (TypeError, KeyError):
            self._rules = []  # malformed legacy payload -> start clean (as before)
        for r in self._rules:
            register_composite(r)
        return list(self._rules)

    def save(self) -> None:
        state_db.save_blob(_COMPOSITES_TABLE, self.path,
                           [composite_to_dict(r) for r in self._rules], db_path=self.db)

    def add(self, rule: CompositeRule) -> None:
        self._rules.append(rule)
        register_composite(rule)
        self.save()

    def remove(self, name: str) -> None:
        self._rules = [r for r in self._rules if r.name != name]
        _COMPOSITES.pop(name, None)
        self.save()

    def names(self) -> list[str]:
        return [r.name for r in self._rules]


def screen(symbol_closes: dict, rule, *, symbol_volumes: dict | None = None,
           min_volume: float = 0.0) -> list[ScreenRow]:
    """Run ``rule`` (a ScreenRule, CompositeRule, or a bare fn) across ``{symbol: closes}``.

    Rows are grouped long, short, neutral; WITHIN each group they rank by setup STRENGTH
    (distance into the favourable tail), not raw value — so the strongest candidate is on top
    whether the rule is long-on-low (RSI/Z-score) or long-on-high (SMA-trend/ROC).

    When ``symbol_volumes`` is given and ``min_volume > 0``, symbols whose mean volume falls
    below ``min_volume`` are dropped before any rows are built; otherwise every symbol is kept.
    """
    fn = getattr(rule, "fn", rule)
    long_low = getattr(rule, "long_low", False)
    rows: list[ScreenRow] = []
    for sym, closes in symbol_closes.items():
        if not closes:
            continue
        if symbol_volumes is not None and min_volume > 0.0:
            vols = symbol_volumes.get(sym)
            if not vols or statistics.fmean(vols) < min_volume:
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
