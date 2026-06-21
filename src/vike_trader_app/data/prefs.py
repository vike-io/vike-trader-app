"""App-wide user preferences, persisted in the app SQLite DB (per the state-in-DB rule).

A tiny key/value document — *not* a new JSON file store. It rides the shared single-row blob
table idiom from :mod:`.state_db`, so a preference survives restarts and is shared across the GUI
and MCP processes via the same DB. There is no legacy JSON file to migrate (this store is new), so
the legacy path is a never-existing sentinel and the one-time sweep is a no-op.

Keep this import-light (stdlib + :mod:`.state_db` only) so reading a single preference never drags
in the pandas-heavy run store.
"""

from __future__ import annotations

from . import state_db

#: Single-row blob table holding the whole preference document.
_TABLE = "app_prefs"
#: No legacy file ever existed for this store; ``state_db`` treats an absent path as "nothing to sweep".
_LEGACY = "storage/app_prefs.json"


def get_pref(key: str, default=None, *, db_path: str | None = None):
    """Return preference ``key`` (or ``default`` when unset / the store is unreadable)."""
    doc = state_db.load_blob(_TABLE, _LEGACY, db_path=db_path) or {}
    return doc.get(key, default)


def set_pref(key: str, value, *, db_path: str | None = None) -> None:
    """Persist ``value`` under preference ``key`` (read-modify-write of the single-row document)."""
    doc = state_db.load_blob(_TABLE, _LEGACY, db_path=db_path) or {}
    doc[key] = value
    state_db.save_blob(_TABLE, _LEGACY, doc, db_path=db_path)
