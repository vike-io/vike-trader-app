"""SQLite-backed list of saved news feeds (named filter presets).

Why a database: per the project rule, **runtime state lives in the app's SQLite store**
(``storage/db/vike_trader_app.sqlite``), never in loose JSON files. Each saved feed is one
``news_feeds`` row — name + the full dataclass as a JSON payload (one codec, no drift). The
store keeps the JSON file's whole-list semantics: every save rewrites the table (a handful of
rows) in list order and reads come back in rowid order, so add/replace/remove behave exactly
as before. The legacy ``storage/news_feeds.json`` is swept into the DB once, then deleted; a
file that fails to parse is left in place — saved feeds are user-authored presets, not a
refetchable cache (mirrors :mod:`vike_trader_app.data.instrument_db`'s treatment of profiles).

Connections are opened per call with a busy timeout and transactions stay tiny — nothing holds
the DB open (the per-call idiom of :mod:`vike_trader_app.ai.telemetry`): the app DB file is
shared with other writers in this process and others.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

#: Where the legacy JSON store lived — read only by the one-time sweep.
DEFAULT_PATH = "storage/news_feeds.json"

#: Default DB file == the app DB (``data.store.DEFAULT_PATH``). A literal so this module stays
#: import-light (mirrors :mod:`vike_trader_app.ai.telemetry` / :mod:`..calendar.store`).
DB_DEFAULT = "storage/db/vike_trader_app.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_feeds (
    name    TEXT PRIMARY KEY,
    payload TEXT NOT NULL  -- the SavedFeed dataclass as JSON
);
"""

# (db, legacy file) pairs swept this process. The sweep itself is idempotent — the memo just
# keeps every load/save from re-statting the legacy file.
_MIGRATED: set[tuple[str, str]] = set()


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
    """The saved-feed list in the app DB; every mutation persists immediately.

    ``path`` is the *legacy* JSON file — read only by the one-time sweep (kept as the first
    positional argument so existing callers don't change). ``db_path`` is the test seam:
    point it at a tmp file; it defaults to the shared app DB. Public API (``load`` / ``save``
    / ``add`` / ``remove`` / ``feeds``) is unchanged from the JSON-file store.
    """

    def __init__(self, path: str = DEFAULT_PATH, *, db_path: str | Path | None = None):
        self.path = Path(path)
        self.db = Path(db_path) if db_path is not None else Path(DB_DEFAULT)
        self._feeds: list[SavedFeed] = []
        self.load()

    # -- connection + one-time legacy sweep ----------------------------------------------

    def _open(self) -> sqlite3.Connection:
        """Open the DB (creating dir + schema), sweeping the legacy JSON file once first.

        ``timeout=5`` is the cross-process busy timeout shared by all app-DB writers. The memo
        is added only after a successful sweep so a transient failure is retried on the next
        call.
        """
        key = (os.fspath(self.db.resolve()), os.fspath(self.path.resolve()))
        self.db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db, timeout=5)
        conn.executescript(_SCHEMA)
        conn.commit()
        if key not in _MIGRATED:
            try:
                self._migrate_legacy_file(conn)
                _MIGRATED.add(key)
            except Exception:
                conn.close()
                raise
        return conn

    def _migrate_legacy_file(self, conn: sqlite3.Connection) -> None:
        """Import the legacy JSON list into ``news_feeds`` (DB rows win), then delete the file.

        An unparseable file is left in place (and logged) so the user can recover their
        hand-saved presets; nothing reads it in normal operation after this.
        """
        if not self.path.is_file():
            return
        try:
            feeds = [SavedFeed(**d)
                     for d in json.loads(self.path.read_text(encoding="utf-8"))]
        except (json.JSONDecodeError, TypeError, OSError):
            log.warning("news-feeds migration: leaving unreadable %s in place", self.path)
            return
        if feeds:
            with conn:
                conn.executemany(
                    "INSERT OR IGNORE INTO news_feeds (name, payload) VALUES (?, ?)",
                    [(f.name, json.dumps(asdict(f))) for f in feeds])
        try:
            self.path.unlink()
        except OSError as exc:
            log.warning("news-feeds migration: could not delete %s (%s)", self.path, exc)
        log.info("news-feeds migration: moved %d saved feed(s) into the app DB", len(feeds))

    # -- public API (signatures unchanged from the JSON-file store) ----------------------

    def load(self) -> None:
        self._feeds = []
        try:
            with closing(self._open()) as conn:
                rows = conn.execute(
                    "SELECT payload FROM news_feeds ORDER BY rowid").fetchall()
            self._feeds = [SavedFeed(**json.loads(r[0])) for r in rows]
        except (sqlite3.Error, json.JSONDecodeError, TypeError, OSError):
            self._feeds = []

    def save(self) -> None:
        rows = [(f.name, json.dumps(asdict(f))) for f in self._feeds]
        with closing(self._open()) as conn, conn:  # one tx: rewrite whole (tiny) list in order
            conn.execute("DELETE FROM news_feeds")
            conn.executemany("INSERT INTO news_feeds (name, payload) VALUES (?, ?)", rows)

    def add(self, feed: SavedFeed) -> None:
        self._feeds = [f for f in self._feeds if f.name != feed.name]   # replace by name
        self._feeds.append(feed)
        self.save()

    def remove(self, name: str) -> None:
        self._feeds = [f for f in self._feeds if f.name != name]
        self.save()

    def feeds(self) -> list[SavedFeed]:
        return list(self._feeds)
