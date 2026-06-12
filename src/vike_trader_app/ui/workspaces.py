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


_SPACE_INDEX = {"chart": 0, "studio": 1}
_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"}


def workspace_from_agent_spec(spec: dict, *, default_interval: str = "1h") -> SessionState:
    """Build a SessionState from the agent's ``create_workspace`` tool args — DEFENSIVELY, since
    the spec is LLM-produced: unknown space -> Chart, bad interval -> default, link group clamped
    to 0-6, missing symbol skipped. The result feeds the same ``_apply_workspace_state`` path as
    a saved workspace, so an agent layout and a hand-saved one are indistinguishable downstream."""
    spec = spec or {}

    def _grp(v) -> int:
        try:
            g = int(v)
        except (TypeError, ValueError):
            return 0
        return g if 0 <= g <= 6 else 0

    documents = []
    for d in (spec.get("documents") or []):
        if not isinstance(d, dict):
            continue
        symbol = str(d.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        interval = d.get("interval")
        interval = interval if interval in _INTERVALS else default_interval
        inds = [{"name": str(n)} for n in (d.get("indicators") or []) if str(n).strip()]
        documents.append({"symbol": symbol, "interval": interval,
                          "link_group": _grp(d.get("link_group")), "indicators": inds})

    panels_in = spec.get("panels") if isinstance(spec.get("panels"), dict) else {}
    panels = {k: bool(panels_in[k]) for k in ("market", "trades") if k in panels_in}
    panels.setdefault("backtester", True)

    return SessionState(
        space=_SPACE_INDEX.get(str(spec.get("space", "")).lower(), 0),
        panels=panels,
        watchlist_link=_grp(spec.get("watchlist_link")),
        documents=documents,
    )


class WorkspaceStore:
    """User workspaces persisted to a JSON file, layered over the built-in defaults."""

    _MAX_RECENTS = 8

    def __init__(self, path: str | os.PathLike | None):
        # path=None -> in-memory only (no persistence): used when the session file is disabled
        # (the offscreen test suite) so a workspace save never touches real storage.
        self._path = Path(path) if path else None
        self._user: dict[str, dict] = {}
        self._recents: list[str] = []
        self._read()

    def _read(self) -> None:
        if self._path is None:
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            ws = raw.get("workspaces", {})
            self._user = {k: v for k, v in ws.items()
                          if isinstance(k, str) and isinstance(v, dict)}
            rec = raw.get("recents", [])
            self._recents = [n for n in rec if isinstance(n, str)][: self._MAX_RECENTS]
        except Exception:  # noqa: BLE001 - missing / corrupt -> just the built-ins
            self._user, self._recents = {}, []

    def _write(self) -> bool:
        if self._path is None:
            return True               # in-memory: the save lives only for this window's lifetime
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")   # matches session.save_session
            tmp.write_text(json.dumps({"version": _WS_VERSION, "workspaces": self._user,
                                       "recents": self._recents},
                                      indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
            return True
        except Exception:  # noqa: BLE001 - a failed write must never crash the shell
            return False

    # --- recents (S4: the File > Recent Workspaces MRU) --------------------------------------

    def recents(self) -> list[str]:
        """Most-recently-opened workspace names (existing ones only, newest first)."""
        known = set(self.names())
        return [n for n in self._recents if n in known]

    def record_recent(self, name: str) -> None:
        if name in self._recents:
            self._recents.remove(name)
        self._recents.insert(0, name)
        del self._recents[self._MAX_RECENTS:]
        self._write()

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
