"""Persisted Historical-Providers chain: which data providers are enabled and in what order.

Mirrors Wealth-Lab's 'Historical Providers' tab — a checkbox (enabled) + a top-to-bottom
priority order. Per the state-in-DB rule the config lives in the app DB (table
``historical_providers``) as a single-row JSON payload — the document is read and written whole
exactly like the legacy ``<root>/providers.json``, which is swept in once, then deleted (an
unreadable file is left in place; see :mod:`.state_db`). No row means "no config saved" and
loads the default, preserving the no-file behavior. The DB is derived from ``root``
(``<root>/db/vike_trader_app.sqlite``), so ``root`` stays the only seam callers/tests need.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import state_db

_TABLE = "historical_providers"

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
    """Where the legacy JSON config lived — read only by the one-time sweep."""
    return Path(root) / "providers.json"


def save_providers_config(cfg: ProvidersConfig, root: str) -> None:
    state_db.save_blob(_TABLE, providers_config_path(root),
                       [asdict(p) for p in cfg.providers])


def load_providers_config(root: str) -> ProvidersConfig:
    payload = state_db.load_blob(_TABLE, providers_config_path(root))
    if payload is None:
        return ProvidersConfig.default()
    saved = [ProviderEntry(d["name"], bool(d.get("enabled", True)), d.get("settings", {}))
             for d in payload]
    seen = {p.name for p in saved}
    # Append any provider that exists today but wasn't in the saved config (disabled, at the
    # end), so a config written by an older build still lists every current provider.
    for name in DEFAULT_ORDER:
        if name not in seen:
            saved.append(ProviderEntry(name, False))
    return ProvidersConfig(saved)
