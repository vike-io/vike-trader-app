"""Persisted Streaming-Providers list: which data sources offer push WebSocket vs poll-only.

Mirrors Wealth-Lab's 'Streaming Providers' tab — informational push/poll classification plus a
per-source enable toggle. Per the state-in-DB rule the config lives in the app DB (table
``streaming_providers``) as a single-row JSON payload — the document is read and written whole
exactly like the legacy ``<root>/streaming_providers.json``, which is swept in once, then
deleted (an unreadable file is left in place; see :mod:`.state_db`). The DB is derived from
``root`` (``<root>/db/vike_trader_app.sqlite``), so ``root`` stays the only seam callers/tests
need. Non-breaking: no config row = nothing changes (live routing remains owned by
select_source).
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import state_db
from .providers_config import DEFAULT_ORDER

_TABLE = "streaming_providers"


@dataclass
class StreamingProviderEntry:
    name: str
    enabled: bool = True


@dataclass
class StreamingProvidersConfig:
    providers: list[StreamingProviderEntry] = field(default_factory=list)

    @classmethod
    def default(cls) -> "StreamingProvidersConfig":
        return cls([StreamingProviderEntry(name, True) for name in default_streaming_providers()])

    def enabled_in_order(self) -> list[str]:
        return [p.name for p in self.providers if p.enabled]


def default_streaming_providers() -> list[str]:
    """The selectable data providers, in display order (matches providers_config.DEFAULT_ORDER)."""
    return list(DEFAULT_ORDER)


def streaming_kind(name: str) -> str:
    """Return 'push' if the source has a live WebSocket, 'poll' otherwise.

    Looks up ``name`` in ``sources.SOURCES``; falls back to 'poll' for unknown names.
    """
    from .sources import SOURCES

    src = SOURCES.get(name)
    if src is None:
        return "poll"
    return "push" if src.supports_live_ws else "poll"


def streaming_providers_path(root: str) -> Path:
    """Where the legacy JSON config lived — read only by the one-time sweep."""
    return Path(root) / "streaming_providers.json"


def save_streaming_providers_config(cfg: StreamingProvidersConfig, root: str) -> None:
    state_db.save_blob(_TABLE, streaming_providers_path(root),
                       [asdict(p) for p in cfg.providers])


def load_streaming_providers_config(root: str) -> StreamingProvidersConfig:
    payload = state_db.load_blob(_TABLE, streaming_providers_path(root))
    if payload is None:
        return StreamingProvidersConfig.default()
    saved = [StreamingProviderEntry(d["name"], bool(d.get("enabled", True)))
             for d in payload]
    seen = {p.name for p in saved}
    # Back-compat: append any provider present today but absent from the saved config (disabled).
    for name in default_streaming_providers():
        if name not in seen:
            saved.append(StreamingProviderEntry(name, False))
    return StreamingProvidersConfig(saved)
