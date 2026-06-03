"""Persisted Streaming-Providers list: which data sources offer push WebSocket vs poll-only.

Mirrors Wealth-Lab's 'Streaming Providers' tab — informational push/poll classification plus
a per-source enable toggle. Stored as human-editable JSON beside the other config files.
Non-breaking: no config file = nothing changes (live routing remains owned by select_source).
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .providers_config import DEFAULT_ORDER


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
    return Path(root) / "streaming_providers.json"


def save_streaming_providers_config(cfg: StreamingProvidersConfig, root: str) -> None:
    path = streaming_providers_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(p) for p in cfg.providers]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_streaming_providers_config(root: str) -> StreamingProvidersConfig:
    path = streaming_providers_path(root)
    if not path.exists():
        return StreamingProvidersConfig.default()
    saved = [StreamingProviderEntry(d["name"], bool(d.get("enabled", True)))
             for d in json.loads(path.read_text(encoding="utf-8"))]
    seen = {p.name for p in saved}
    # Back-compat: append any provider present today but absent from the saved file (disabled).
    for name in default_streaming_providers():
        if name not in seen:
            saved.append(StreamingProviderEntry(name, False))
    return StreamingProvidersConfig(saved)
