"""A lightweight trading journal — diary entries persisted in the app DB.

The Journal tab records notes against strategies/symbols + their headline metrics. Why a
database: per the project rule, runtime state lives in the app's SQLite store, never in loose
JSON files. The entry list is one ``journal`` row — the whole list as a single JSON payload,
read and written whole exactly like the legacy file (the single-row-blob judgment; see
:mod:`vike_trader_app.data.state_db`). The legacy ``storage/journal.json`` is swept in once,
then deleted; an unreadable legacy file is left in place — journal entries are user-authored.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..data import state_db

#: Where the legacy JSON store lived — read only by the one-time sweep.
DEFAULT_PATH = "storage/journal.json"

_TABLE = "journal"


@dataclass
class JournalEntry:
    """One journal note. ``ts`` is epoch ms; ``metrics`` is an optional free-form dict."""

    ts: int
    title: str
    symbol: str = ""
    strategy: str = ""
    notes: str = ""
    tags: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


class Journal:
    """The journal-entry list in the app DB (table ``journal``); every mutation persists
    immediately.

    ``path`` is the *legacy* JSON file — read only by the one-time sweep (kept as the first
    positional argument so existing callers don't change); the DB lives beside it
    (``<dir>/db/vike_trader_app.sqlite`` — the shared app DB for the default path). ``db_path``
    is the explicit test seam. Public API is unchanged from the JSON-file store.
    """

    def __init__(self, path: str = DEFAULT_PATH, *, db_path: str | Path | None = None):
        self.path = Path(path)
        self.db = Path(db_path) if db_path is not None else state_db.db_for_file(path)
        self._entries: list[JournalEntry] = []
        self.load()

    def load(self) -> None:
        payload = state_db.load_blob(_TABLE, self.path, db_path=self.db)
        try:
            self._entries = [JournalEntry(**d) for d in payload or []]
        except TypeError:
            self._entries = []  # malformed legacy payload -> start clean rather than crash

    def save(self) -> None:
        state_db.save_blob(_TABLE, self.path, [asdict(e) for e in self._entries],
                           db_path=self.db)

    def add(self, entry: JournalEntry) -> None:
        self._entries.append(entry)
        self.save()

    def remove(self, index: int) -> None:
        if 0 <= index < len(self._entries):
            del self._entries[index]
            self.save()

    def entries(self) -> list[JournalEntry]:
        """Entries, newest first."""
        return sorted(self._entries, key=lambda e: e.ts, reverse=True)

    def entries_indexed(self):
        """``(store_index, entry)`` pairs, newest first — a stable display->store mapping."""
        return sorted(enumerate(self._entries), key=lambda iv: iv[1].ts, reverse=True)
