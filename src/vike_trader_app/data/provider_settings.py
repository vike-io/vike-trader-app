"""Per-provider settings schema (Wealth-Lab's typed Parameter form, data-driven).

Each provider declares a list of typed fields; the UI auto-builds a form and persists the values
into providers.json. Secrets use env-var indirection: an ``api_key_env`` field stores the NAME of
an environment variable, and the value is read from the environment at fetch time (never persisted).
"""
from dataclasses import dataclass


@dataclass
class FieldSpec:
    name: str
    kind: str           # "str" | "int" | "float" | "bool" | "choice"
    default: object = ""
    hint: str = ""
    choices: list | None = None


# All five crypto REST sources accept base_url and pause in their fetch_bars_range.
# dukascopy and yahoo use fetch_hour/fetch_chart injection (no base_url/pause) so they get no fields.
_REST = [
    FieldSpec("pause", "float", 0.0, "seconds to wait between requests (avoid rate-limit 429s)"),
    FieldSpec("base_url", "str", "", "override the API endpoint (regional / proxy); blank = default"),
    FieldSpec("api_key_env", "str", "", "NAME of an env var holding the API key (value read at fetch time)"),
]

PROVIDER_SETTINGS: dict[str, list[FieldSpec]] = {
    "binance": list(_REST),
    "bybit": list(_REST),
    "okx": list(_REST),
    "coinbase": list(_REST),
    "kraken": list(_REST),
    "dukascopy": [],
    "yahoo": [],
}


def fields_for(provider: str) -> list[FieldSpec]:
    """Return the list of FieldSpec for ``provider`` (empty list if unknown or no settings)."""
    return PROVIDER_SETTINGS.get(provider, [])


def defaults_for(provider: str) -> dict:
    """Return ``{name: default}`` for every field declared for ``provider``."""
    return {f.name: f.default for f in fields_for(provider)}
