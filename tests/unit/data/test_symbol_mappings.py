"""Tests for per-provider symbol mappings (Part 1: model + store)."""

import pytest

from vike_trader_app.data.symbol_mappings import (
    MappingRule,
    SymbolMappings,
    apply_mapping,
    load_mappings,
    save_mappings,
)


# ---------------------------------------------------------------------------
# apply_mapping — literal rules
# ---------------------------------------------------------------------------

def test_literal_map_applies_for_its_provider():
    m = SymbolMappings([MappingRule("yahoo", "BRK.B", "BRK-B")])
    assert apply_mapping("BRK.B", "yahoo", m) == "BRK-B"


def test_literal_map_does_not_apply_to_other_provider():
    m = SymbolMappings([MappingRule("yahoo", "BRK.B", "BRK-B")])
    # A different provider should not get the mapping
    assert apply_mapping("BRK.B", "binance", m) == "BRK.B"


def test_literal_map_is_case_insensitive():
    m = SymbolMappings([MappingRule("yahoo", "brk.b", "BRK-B")])
    assert apply_mapping("BRK.B", "yahoo", m) == "BRK-B"
    assert apply_mapping("brk.b", "yahoo", m) == "BRK-B"


def test_unmatched_symbol_returned_unchanged():
    m = SymbolMappings([MappingRule("yahoo", "BRK.B", "BRK-B")])
    assert apply_mapping("AAPL", "yahoo", m) == "AAPL"


def test_empty_mappings_symbol_unchanged():
    m = SymbolMappings()
    assert apply_mapping("BRK.B", "yahoo", m) == "BRK.B"


def test_first_matching_rule_wins():
    """Only the first matching rule for a provider is applied."""
    m = SymbolMappings([
        MappingRule("yahoo", "BRK.B", "BRK-B"),
        MappingRule("yahoo", "BRK.B", "SOMETHING-ELSE"),
    ])
    assert apply_mapping("BRK.B", "yahoo", m) == "BRK-B"


# ---------------------------------------------------------------------------
# apply_mapping — regex rules
# ---------------------------------------------------------------------------

def test_regex_fullmatch_applies():
    """Regex rule with fullmatch; backreference in replacement."""
    m = SymbolMappings([MappingRule("yahoo", r"(\w+)\.(\w+)", r"\1-\2", is_regex=True)])
    assert apply_mapping("BRK.B", "yahoo", m) == "BRK-B"


def test_regex_partial_no_match():
    """fullmatch means the entire string must match the pattern."""
    m = SymbolMappings([MappingRule("yahoo", r"BRK", r"BRK-FULL", is_regex=True)])
    # 'BRK' does not fullmatch 'BRK.B'
    assert apply_mapping("BRK.B", "yahoo", m) == "BRK.B"


def test_regex_does_not_apply_to_other_provider():
    m = SymbolMappings([MappingRule("yahoo", r"(\w+)\.(\w+)", r"\1-\2", is_regex=True)])
    assert apply_mapping("BRK.B", "dukascopy", m) == "BRK.B"


# ---------------------------------------------------------------------------
# Cycle-safety (single-pass)
# ---------------------------------------------------------------------------

def test_cycle_safety_single_pass():
    """A->B and B->C rules must NOT chain: apply_mapping('A', p, m) == 'B', not 'C'.

    Single-pass guarantee: a replacement is never re-evaluated against further rules.
    """
    m = SymbolMappings([
        MappingRule("yahoo", "A", "B"),
        MappingRule("yahoo", "B", "C"),
    ])
    # 'A' matches first rule → 'B'. Should stop there, not apply second rule.
    assert apply_mapping("A", "yahoo", m) == "B"
    # 'B' matches second rule → 'C' (direct call still works for B itself)
    assert apply_mapping("B", "yahoo", m) == "C"


# ---------------------------------------------------------------------------
# Round-trip: save / load
# ---------------------------------------------------------------------------

def test_round_trip_literal_rule(tmp_path):
    m = SymbolMappings([MappingRule("yahoo", "BRK.B", "BRK-B", is_regex=False)])
    save_mappings(m, str(tmp_path))
    loaded = load_mappings(str(tmp_path))
    assert len(loaded.rules) == 1
    r = loaded.rules[0]
    assert r.provider == "yahoo"
    assert r.pattern == "BRK.B"
    assert r.replacement == "BRK-B"
    assert r.is_regex is False


def test_round_trip_regex_rule(tmp_path):
    m = SymbolMappings([MappingRule("yahoo", r"(\w+)\.(\w+)", r"\1-\2", is_regex=True)])
    save_mappings(m, str(tmp_path))
    loaded = load_mappings(str(tmp_path))
    assert len(loaded.rules) == 1
    r = loaded.rules[0]
    assert r.is_regex is True
    assert apply_mapping("BRK.B", "yahoo", loaded) == "BRK-B"


def test_round_trip_multiple_rules(tmp_path):
    rules = [
        MappingRule("yahoo", "BRK.B", "BRK-B"),
        MappingRule("yahoo", "BF.B", "BF-B"),
        MappingRule("binance", "BTCUSDT", "BTC-USDT"),
    ]
    m = SymbolMappings(rules)
    save_mappings(m, str(tmp_path))
    loaded = load_mappings(str(tmp_path))
    assert len(loaded.rules) == 3
    assert loaded.rules[2].provider == "binance"


def test_load_missing_file_returns_empty_mappings(tmp_path):
    loaded = load_mappings(str(tmp_path))
    assert loaded.rules == []


def test_save_creates_parent_dirs(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    m = SymbolMappings([MappingRule("yahoo", "BRK.B", "BRK-B")])
    save_mappings(m, str(deep))
    loaded = load_mappings(str(deep))
    assert len(loaded.rules) == 1


def test_mappings_file_path(tmp_path):
    from vike_trader_app.data.symbol_mappings import symbol_mappings_path
    p = symbol_mappings_path(str(tmp_path))
    assert p.name == "symbol_mappings.json"
    assert p.parent == tmp_path


# --- state-in-DB migration ---

def test_save_persists_to_app_db_not_json(tmp_path):
    """State-in-DB rule: save writes the app DB under <root>/db, never a loose JSON file."""
    from vike_trader_app.data.symbol_mappings import symbol_mappings_path

    save_mappings(SymbolMappings([MappingRule("yahoo", "BRK.B", "BRK-B")]), str(tmp_path))
    assert not symbol_mappings_path(str(tmp_path)).exists()
    assert (tmp_path / "db" / "vike_trader_app.sqlite").exists()


def test_legacy_mappings_json_migrates_into_db_then_file_deleted(tmp_path):
    """One-time sweep: a legacy symbol_mappings.json is imported, then removed."""
    import json

    from vike_trader_app.data.symbol_mappings import symbol_mappings_path

    legacy = symbol_mappings_path(str(tmp_path))
    legacy.write_text(json.dumps([
        {"provider": "yahoo", "pattern": "BRK.B", "replacement": "BRK-B", "is_regex": False},
    ]), encoding="utf-8")
    loaded = load_mappings(str(tmp_path))
    assert apply_mapping("BRK.B", "yahoo", loaded) == "BRK-B"   # rule survived the migration
    assert not legacy.exists()                                  # legacy file deleted
