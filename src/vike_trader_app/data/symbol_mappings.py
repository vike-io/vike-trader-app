"""Per-provider symbol mappings (Wealth-Lab's Symbol Mapper).

Rewrites a symbol to a provider's symbology at fetch time. A rule matches one provider; literal
rules require an exact (case-insensitive) match of the source symbol, regex rules use re.fullmatch.
Single-pass application (no chaining) guards against cycles. Stored as JSON under config_root.
"""
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


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
    return Path(root) / "symbol_mappings.json"


def save_mappings(m: SymbolMappings, root: str) -> None:
    path = symbol_mappings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(r) for r in m.rules], indent=2), encoding="utf-8")


def load_mappings(root: str) -> SymbolMappings:
    path = symbol_mappings_path(root)
    if not path.exists():
        return SymbolMappings()
    data = json.loads(path.read_text(encoding="utf-8"))
    return SymbolMappings([MappingRule(d["provider"], d["pattern"], d["replacement"],
                                       bool(d.get("is_regex", False))) for d in data])


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
