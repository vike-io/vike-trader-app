"""Per-provider symbol mappings (Wealth-Lab's Symbol Mapper).

Rewrites a symbol to a provider's symbology at fetch time. A rule matches one provider; literal
rules require an exact (case-insensitive) match of the source symbol, regex rules use re.fullmatch.
Single-pass application (no chaining) guards against cycles.

Per the state-in-DB rule the rule list lives in the app DB (table ``symbol_mappings``) as a
single-row JSON payload — the document is read and written whole exactly like the legacy
``<root>/symbol_mappings.json``, which is swept in once, then deleted (an unreadable file is
left in place; see :mod:`.state_db`). The DB is derived from ``root``
(``<root>/db/vike_trader_app.sqlite``), so ``root`` stays the only seam callers/tests need.
"""
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import state_db

_TABLE = "symbol_mappings"


@dataclass
class MappingRule:
    provider: str
    pattern: str            # source symbol (literal) or a regex
    replacement: str        # provider symbol (or regex replacement with backrefs)
    is_regex: bool = False


@dataclass
class SymbolMappings:
    rules: list[MappingRule] = field(default_factory=list)


def symbol_mappings_path(root: str) -> Path:
    """Where the legacy JSON store lived — read only by the one-time sweep."""
    return Path(root) / "symbol_mappings.json"


def save_mappings(m: SymbolMappings, root: str) -> None:
    state_db.save_blob(_TABLE, symbol_mappings_path(root), [asdict(r) for r in m.rules])


def load_mappings(root: str) -> SymbolMappings:
    payload = state_db.load_blob(_TABLE, symbol_mappings_path(root))
    if payload is None:
        return SymbolMappings()
    return SymbolMappings([MappingRule(d["provider"], d["pattern"], d["replacement"],
                                       bool(d.get("is_regex", False))) for d in payload])


def apply_mapping(symbol: str, provider: str, m: SymbolMappings) -> str:
    """Return the provider-specific symbol for ``symbol`` (the first matching rule for ``provider``),
    or ``symbol`` unchanged. Single pass — a rule's replacement is never re-mapped (cycle-safe)."""
    for r in m.rules:
        if r.provider != provider:
            continue
        if r.is_regex:
            if re.fullmatch(r.pattern, symbol):
                return re.sub(r.pattern, r.replacement, symbol)
        elif r.pattern.upper() == symbol.upper():
            return r.replacement
    return symbol
