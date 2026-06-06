"""Local watchlist alerts — saved (symbol, rule, direction) checks evaluated against the cache.

Self-contained (file-backed JSON, like the journal). Reuses ``analysis.screener`` rules: an alert
fires when a symbol's current screener signal matches the alert's direction. No network, no
``data/`` writes, no MCP — the UI loads cached closes and calls ``evaluate``. Notification is
in-app (the desktop app can't reach a remote alert service); a system-tray toast is a follow-up.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import screener
from .screener import RULES

DEFAULT_PATH = "storage/alerts.json"
_RULES_BY_NAME = {r.name: r for r in RULES}


@dataclass
class AlertRule:
    """Notify when ``symbol``'s ``rule`` signal matches ``direction`` ("long"/"short"/"any")."""

    symbol: str
    rule: str
    direction: str = "any"
    note: str = ""


@dataclass(frozen=True)
class AlertHit:
    """The evaluation of one AlertRule against current data."""

    rule: AlertRule
    triggered: bool
    signal: str
    value: float


class AlertStore:
    """A JSON-file-backed list of ``AlertRule``; every mutation persists immediately."""

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = Path(path)
        self._rules: list[AlertRule] = []
        self.load()

    def load(self) -> None:
        self._rules = []
        if self.path.exists():
            try:
                self._rules = [AlertRule(**d) for d in json.loads(self.path.read_text(encoding="utf-8"))]
            except (json.JSONDecodeError, TypeError, OSError):
                self._rules = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(r) for r in self._rules], indent=2), encoding="utf-8")

    def add(self, rule: AlertRule) -> None:
        self._rules.append(rule)
        self.save()

    def remove(self, index: int) -> None:
        if 0 <= index < len(self._rules):
            del self._rules[index]
            self.save()

    def rules(self) -> list[AlertRule]:
        return list(self._rules)


def evaluate(rules, symbol_closes: dict) -> list[AlertHit]:
    """Run each rule's screener fn on its symbol's closes; flag hits matching the direction."""
    hits: list[AlertHit] = []
    for ar in rules:
        closes = symbol_closes.get(ar.symbol) or []
        spec = screener.rule_by_name(ar.rule)   # resolves base rules AND registered composites
        if not closes or spec is None:
            hits.append(AlertHit(ar, False, "neutral", 0.0))
            continue
        fn = getattr(spec, "fn", spec)   # ScreenRule has .fn; CompositeRule is callable itself
        signal, value = fn(closes)
        triggered = (signal != "neutral") if ar.direction == "any" else (signal == ar.direction)
        hits.append(AlertHit(ar, triggered, signal, value))
    return hits
