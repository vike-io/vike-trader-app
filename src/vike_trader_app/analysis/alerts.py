"""Local watchlist alerts — saved (symbol, rule, direction) checks evaluated against the cache.

Reuses ``analysis.screener`` rules: an alert fires when a symbol's current screener signal
matches the alert's direction. No network, no ``data/`` writes, no MCP — the UI loads cached
closes and calls ``evaluate``. Notification is in-app (the desktop app can't reach a remote
alert service); a system-tray toast is a follow-up.

Why a database: per the project rule, runtime state lives in the app's SQLite store, never in
loose JSON files. The alert list is one ``alerts`` row — the whole list as a single JSON payload,
read and written whole exactly like the legacy file (the single-row-blob judgment; see
:mod:`vike_trader_app.data.state_db`). The legacy ``storage/alerts.json`` is swept in once, then
deleted; an unreadable legacy file is left in place — alerts are user-authored.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from ..data import state_db
from . import screener

#: Where the legacy JSON store lived — read only by the one-time sweep.
DEFAULT_PATH = "storage/alerts.json"

_TABLE = "alerts"


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
    """The alert list in the app DB (table ``alerts``); every mutation persists immediately.

    ``path`` is the *legacy* JSON file — read only by the one-time sweep (kept as the first
    positional argument so existing callers don't change); the DB lives beside it
    (``<dir>/db/vike_trader_app.sqlite`` — the shared app DB for the default path). ``db_path``
    is the explicit test seam. Public API is unchanged from the JSON-file store.
    """

    def __init__(self, path: str = DEFAULT_PATH, *, db_path: str | Path | None = None):
        self.path = Path(path)
        self.db = Path(db_path) if db_path is not None else state_db.db_for_file(path)
        self._rules: list[AlertRule] = []
        self.load()

    def load(self) -> None:
        payload = state_db.load_blob(_TABLE, self.path, db_path=self.db)
        try:
            self._rules = [AlertRule(**d) for d in payload or []]
        except TypeError:
            self._rules = []  # malformed legacy payload -> start clean (as before)

    def save(self) -> None:
        state_db.save_blob(_TABLE, self.path, [asdict(r) for r in self._rules],
                           db_path=self.db)

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
