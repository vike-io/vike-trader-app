"""Persisted Event-Providers list: which event data sources (news + calendar) are enabled and
in what order.

Mirrors the 'Event Providers' tab concept from Wealth-Lab. Stored as human-editable JSON beside
the other config files. Non-breaking: when no config file exists, enabled_event_providers()
returns None, which callers interpret as "everything on" — identical to today's behavior.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


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
    return Path(root) / "event_providers.json"


def save_event_providers_config(cfg: EventProvidersConfig, root: str) -> None:
    path = event_providers_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(p) for p in cfg.providers]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_event_providers_config(root: str) -> EventProvidersConfig:
    path = event_providers_path(root)
    if not path.exists():
        return EventProvidersConfig.default()
    saved = [EventProviderEntry(d["name"], bool(d.get("enabled", True)))
             for d in json.loads(path.read_text(encoding="utf-8"))]
    seen = {p.name for p in saved}
    # Back-compat: append any newly-registered provider as enabled when missing from the saved file.
    for name in default_event_providers():
        if name not in seen:
            saved.append(EventProviderEntry(name, True))
    return EventProvidersConfig(saved)


def enabled_event_providers(root: str) -> set[str] | None:
    """Return the enabled provider names if a config file exists, else None.

    None means "no override — everything on", preserving pre-config behavior.
    """
    path = event_providers_path(root)
    if not path.exists():
        return None
    cfg = load_event_providers_config(root)
    return set(cfg.enabled_in_order())
