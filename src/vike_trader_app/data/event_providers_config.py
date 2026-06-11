"""Persisted Event-Providers list: which event data sources (news + calendar) are enabled and
in what order.

Mirrors the 'Event Providers' tab concept from Wealth-Lab. Per the state-in-DB rule the config
lives in the app DB (table ``event_providers``) as a single-row JSON payload — the document is
read and written whole exactly like the legacy ``<root>/event_providers.json``, which is swept
in once, then deleted (an unreadable file is left in place; see :mod:`.state_db`). The DB is
derived from ``root`` (``<root>/db/vike_trader_app.sqlite``), so ``root`` stays the only seam
callers/tests need. Non-breaking: when no config row exists, ``enabled_event_providers()``
returns None, which callers interpret as "everything on" — identical to the no-file behavior.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import state_db

_TABLE = "event_providers"


@dataclass
class EventProviderEntry:
    name: str
    enabled: bool = True


@dataclass
class EventProvidersConfig:
    providers: list[EventProviderEntry] = field(default_factory=list)

    @classmethod
    def default(cls) -> "EventProvidersConfig":
        return cls([EventProviderEntry(name, True) for name in default_event_providers()])

    def enabled_in_order(self) -> list[str]:
        return [p.name for p in self.providers if p.enabled]


def default_event_providers() -> list[str]:
    """The union of news provider names + calendar provider names (ScheduleProvider + ActualsProviders).

    This is the canonical ordered list used when no config file exists yet.
    """
    from .news.providers import PROVIDERS as NEWS_PROVIDERS
    news_names = [spec.name for spec in NEWS_PROVIDERS]

    # Calendar provider names: schedule (ForexFactory) + actuals (FRED, BLS, BEA, Census, ECB)
    # Listed in the same order as default_repository() wires them.
    calendar_names = [
        "ForexFactory",  # ScheduleProvider
        "FRED",
        "BLS",
        "BEA",
        "Census",
        "ECB",
    ]

    # News providers first, then calendar providers (no duplicates)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in news_names + calendar_names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def event_providers_path(root: str) -> Path:
    """Where the legacy JSON config lived — read only by the one-time sweep."""
    return Path(root) / "event_providers.json"


def save_event_providers_config(cfg: EventProvidersConfig, root: str) -> None:
    state_db.save_blob(_TABLE, event_providers_path(root),
                       [asdict(p) for p in cfg.providers])


def _config_from_payload(payload: list) -> EventProvidersConfig:
    saved = [EventProviderEntry(d["name"], bool(d.get("enabled", True))) for d in payload]
    seen = {p.name for p in saved}
    # Back-compat: append any newly-registered provider as enabled when missing from the
    # saved config.
    for name in default_event_providers():
        if name not in seen:
            saved.append(EventProviderEntry(name, True))
    return EventProvidersConfig(saved)


def load_event_providers_config(root: str) -> EventProvidersConfig:
    payload = state_db.load_blob(_TABLE, event_providers_path(root))
    if payload is None:
        return EventProvidersConfig.default()
    return _config_from_payload(payload)


def enabled_event_providers(root: str) -> set[str] | None:
    """Return the enabled provider names if a config was ever saved, else None.

    None means "no override — everything on", preserving pre-config behavior.
    """
    payload = state_db.load_blob(_TABLE, event_providers_path(root))
    if payload is None:
        return None
    return set(_config_from_payload(payload).enabled_in_order())
