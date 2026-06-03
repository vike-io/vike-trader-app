"""Persisted Historical-Providers chain: which data providers are enabled and in what order.

Mirrors Wealth-Lab's 'Historical Providers' tab — a checkbox (enabled) + a top-to-bottom
priority order. Stored as human-editable JSON beside the datasets/pins config.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Individual price providers users can order/enable (the aggregate 'crypto'/'forex'/'vike'
# handles in sources.SOURCES are intentionally excluded — they're routing shortcuts, not endpoints).
DEFAULT_ORDER = ["binance", "bybit", "okx", "coinbase", "kraken", "dukascopy", "yahoo"]


@dataclass
class ProviderEntry:
    name: str
    enabled: bool = True
    settings: dict = field(default_factory=dict)


@dataclass
class ProvidersConfig:
    providers: list[ProviderEntry] = field(default_factory=list)

    @classmethod
    def default(cls) -> "ProvidersConfig":
        return cls([ProviderEntry(name, True) for name in DEFAULT_ORDER])

    def enabled_in_order(self) -> list[str]:
        return [p.name for p in self.providers if p.enabled]


def providers_config_path(root: str) -> Path:
    return Path(root) / "providers.json"


def save_providers_config(cfg: ProvidersConfig, root: str) -> None:
    path = providers_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(p) for p in cfg.providers]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_providers_config(root: str) -> ProvidersConfig:
    path = providers_config_path(root)
    if not path.exists():
        return ProvidersConfig.default()
    saved = [ProviderEntry(d["name"], bool(d.get("enabled", True)), d.get("settings", {}))
             for d in json.loads(path.read_text(encoding="utf-8"))]
    seen = {p.name for p in saved}
    # Append any provider that exists today but wasn't in the saved file (disabled, at the end),
    # so a config written by an older build still lists every current provider.
    for name in DEFAULT_ORDER:
        if name not in seen:
            saved.append(ProviderEntry(name, False))
    return ProvidersConfig(saved)
