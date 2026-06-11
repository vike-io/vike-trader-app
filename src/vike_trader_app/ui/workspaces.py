"""Named workspaces (Phase 4 of the workspace program).

A *workspace* is a named, user-managed snapshot of the shell: which chart documents are open
(symbol/interval/indicators/link colour), the dock layout (ADS ``saveState`` blob), which
panels are open, the active space, and the watchlist link colour. It reuses ``SessionState``
as its payload — a workspace is essentially a named session — so the same recreate-docs ->
restoreState -> re-sync apply path serves both launch-restore and "open workspace".

This module is the Qt-free persistence + built-in defaults (unit-tested without a window).
``WorkspaceStore`` keeps user workspaces in a JSON file; three built-in starter layouts are
always available and can be overridden (and reverted) by saving/deleting a same-named copy.
Built-ins carry no ``dock_state_hex`` — applying one uses the shell's default dock positions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .session import SessionState

_WS_VERSION = 1

# Order shown in the menu; built-ins always listed first.
BUILTIN_NAMES = ["Trading", "Research", "Backtesting"]


def builtin_workspaces() -> dict[str, SessionState]:
    """The starter layouts. Declarative (no dock blob) — apply uses default dock positions."""
    return {
        # Classic single-chart trading desk: Chart space, watchlist + trades open.
        "Trading": SessionState(
            space=0, panels={"backtester": True, "market": True, "trades": True},
        ),
        # Side-by-side research: two linked (blue) chart documents + the watchlist.
        "Research": SessionState(
            space=0, panels={"backtester": True, "market": True, "trades": False},
            watchlist_link=3,
            documents=[
                {"symbol": "ETHUSDT", "interval": "1h", "link_group": 3, "indicators": []},
                {"symbol": "SOLUSDT", "interval": "1h", "link_group": 3, "indicators": []},
            ],
        ),
        # Strategy lab: Studio space with the trades panel for results.
        "Backtesting": SessionState(
            space=1, panels={"backtester": True, "market": False, "trades": True},
        ),
    }


class WorkspaceStore:
    """User workspaces persisted to a JSON file, layered over the built-in defaults."""

    def __init__(self, path: str | os.PathLike | None):
        # path=None -> in-memory only (no persistence): used when the session file is disabled
        # (the offscreen test suite) so a workspace save never touches real storage.
        self._path = Path(path) if path else None
        self._user: dict[str, dict] = self._read()

    def _read(self) -> dict:
        if self._path is None:
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            ws = raw.get("workspaces", {})
            return {k: v for k, v in ws.items() if isinstance(k, str) and isinstance(v, dict)}
        except Exception:  # noqa: BLE001 - missing / corrupt -> just the built-ins
            return {}

    def _write(self) -> bool:
        if self._path is None:
            return True               # in-memory: the save lives only for this window's lifetime
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")   # matches session.save_session
            tmp.write_text(json.dumps({"version": _WS_VERSION, "workspaces": self._user},
                                      indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
            return True
        except Exception:  # noqa: BLE001 - a failed write must never crash the shell
            return False

    def names(self) -> list[str]:
        """Built-ins first (in fixed order), then any extra user workspaces in insertion order."""
        order = list(BUILTIN_NAMES)
        for name in self._user:
            if name not in order:
                order.append(name)
        return order

    def is_builtin(self, name: str) -> bool:
        return name in BUILTIN_NAMES

    def is_user(self, name: str) -> bool:
        return name in self._user

    def load(self, name: str) -> SessionState | None:
        """A user-saved workspace shadows the built-in of the same name; else the built-in."""
        if name in self._user:
            return SessionState.from_dict(self._user[name])
        return builtin_workspaces().get(name)

    def save(self, name: str, state: SessionState) -> bool:
        payload = state.to_dict()
        payload.pop("version", None)   # the schema version belongs in the outer envelope, not
        self._user[name] = payload     # each entry (it's the SESSION version, misleading here)
        return self._write()

    def delete(self, name: str) -> bool:
        """Remove a user workspace. Deleting a user-overridden built-in reverts it to default."""
        if name in self._user:
            del self._user[name]
            return self._write()
        return False

    def rename(self, old: str, new: str) -> bool:
        # Reject a collision with ANY existing name (built-in or user) — renaming onto a
        # built-in would silently shadow it, which is surprising; use save() to override.
        if old in self._user and new and new not in self.names():
            self._user[new] = self._user.pop(old)
            return self._write()
        return False
