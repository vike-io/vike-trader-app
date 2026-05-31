"""A lightweight, file-backed trading journal — diary entries persisted as JSON.

Self-contained on purpose (NOT ``data/store.py``): a list of ``JournalEntry`` saved to a JSON
file. The Journal tab records notes against strategies/symbols + their headline metrics. All I/O
goes through ``path`` so it's trivially testable with a temp file.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_PATH = "storage/journal.json"


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
    """A JSON-file-backed list of ``JournalEntry``; every mutation persists immediately."""

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = Path(path)
        self._entries: list[JournalEntry] = []
        self.load()

    def load(self) -> None:
        self._entries = []
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._entries = [JournalEntry(**d) for d in data]
            except (json.JSONDecodeError, TypeError, OSError):
                self._entries = []  # corrupt/old file -> start clean rather than crash

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(e) for e in self._entries], indent=2),
                             encoding="utf-8")

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
