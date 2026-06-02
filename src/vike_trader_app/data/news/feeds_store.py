"""JSON-file-backed list of saved news feeds (named filter presets). Mirrors AlertStore."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_PATH = "storage/news_feeds.json"


@dataclass
class SavedFeed:
    """A named snapshot of the toolbar's filter state."""

    name: str
    market: str = ""                      # "" = All
    providers: list[str] = field(default_factory=list)
    symbol: str = ""
    query: str = ""
    follow_chart: bool = True


class SavedFeedStore:
    """A JSON-file-backed list of ``SavedFeed``; every mutation persists immediately."""

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = Path(path)
        self._feeds: list[SavedFeed] = []
        self.load()

    def load(self) -> None:
        self._feeds = []
        if self.path.exists():
            try:
                self._feeds = [SavedFeed(**d) for d in json.loads(self.path.read_text(encoding="utf-8"))]
            except (json.JSONDecodeError, TypeError, OSError):
                self._feeds = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(f) for f in self._feeds], indent=2), encoding="utf-8")

    def add(self, feed: SavedFeed) -> None:
        self._feeds = [f for f in self._feeds if f.name != feed.name]   # replace by name
        self._feeds.append(feed)
        self.save()

    def remove(self, name: str) -> None:
        self._feeds = [f for f in self._feeds if f.name != name]
        self.save()

    def feeds(self) -> list[SavedFeed]:
        return list(self._feeds)
