"""Unit tests for the multi-symbol screener (pure logic)."""

import pytest

from vike_trader_app.analysis import screener as S


def test_screen_groups_long_short_neutral_and_sorts():
    rising = [100 + i for i in range(40)]
    falling = [100 - i * 0.5 for i in range(40)]
    flat = [100.0] * 40
    rows = S.screen({"UP": rising, "DN": falling, "FLAT": flat}, S._rule_roc(30))
    sig = {r.symbol: r.signal for r in rows}
    assert sig["UP"] == "long"
    assert sig["DN"] == "short"
    assert sig["FLAT"] == "neutral"
    assert rows[0].signal == "long"        # longs grouped first
    assert rows[-1].signal == "neutral"    # neutrals last


def test_screen_skips_empty_series():
    rows = S.screen({"A": [], "B": [100 + i for i in range(40)]}, S._rule_roc(30))
    assert [r.symbol for r in rows] == ["B"]


def test_rsi_rule_extremes():
    assert S._rule_rsi()(list(range(1, 60)))[0] == "short"        # all gains -> overbought
    assert S._rule_rsi()(list(range(60, 1, -1)))[0] == "long"     # all losses -> oversold


def test_sma_trend_rule_sign():
    assert S._rule_sma_trend(20)([100 + i for i in range(40)])[0] == "long"   # price above SMA
    assert S._rule_sma_trend(20)([100 - i for i in range(40)])[0] == "short"  # price below SMA


def test_rules_catalogue_present():
    assert len(S.RULES) >= 4
    assert all(callable(r.fn) and r.name for r in S.RULES)


def test_ranks_strongest_first_momentum(app=None):
    roc = next(r for r in S.RULES if r.name.startswith("ROC"))
    series = {
        "BIGUP": [100 + i * 1.0 for i in range(60)],     # strong long
        "SMALLUP": [100 + i * 0.05 for i in range(60)],  # weak long
        "SMALLDN": [100 - i * 0.05 for i in range(60)],  # weak short
        "BIGDN": [100 - i * 1.0 for i in range(60)],     # strong short
    }
    rows = S.screen(series, roc)
    assert [r.symbol for r in rows if r.signal == "long"] == ["BIGUP", "SMALLUP"]
    assert [r.symbol for r in rows if r.signal == "short"] == ["BIGDN", "SMALLDN"]  # was inverted


def test_long_low_rule_orders_strongest_oversold_first():
    # a synthetic long-on-low rule: long when value<0; strongest long = lowest value
    rule = S.ScreenRule("fake", "", lambda c: ("long" if c[-1] < 50 else "short", c[-1] - 50.0),
                        long_low=True)
    rows = S.screen({"LOW": [10.0], "HIGH": [40.0]}, rule)   # both long (-40, -10)
    assert [r.symbol for r in rows if r.signal == "long"] == ["LOW", "HIGH"]  # -40 (strongest) first


# --- composite (multi-condition) rules ------------------------------------------------

def _rising(n=60):
    # RSI -> overbought -> "short"; ROC -> positive -> "long"; SMA-trend -> above -> "long"
    return [100 + i for i in range(n)]


def _falling(n=60):
    # RSI -> oversold -> "long"; ROC -> negative -> "short"; SMA-trend -> below -> "short"
    return [100 - i for i in range(n)]


def test_composite_and_fires_only_when_all_match():
    # both conditions long on a falling series (RSI oversold, ROC... is short, so use SMA-trend)
    # Use a rising series: ROC long AND SMA-trend long both satisfied.
    comp = S.CompositeRule(
        "UP momentum",
        "ROC long AND SMA-trend long",
        (S.Condition("ROC(30) momentum", "long"), S.Condition("SMA(50) trend", "long")),
        combine="AND",
        direction="long",
    )
    sig, val = comp(_rising())
    assert sig == "long"
    assert val == 2.0          # both satisfied

    # one condition fails -> neutral on AND
    comp2 = S.CompositeRule(
        "mismatch",
        "ROC long AND RSI long",
        (S.Condition("ROC(30) momentum", "long"), S.Condition("RSI(14) 30/70", "long")),
        combine="AND",
        direction="long",
    )
    sig2, val2 = comp2(_rising())   # ROC long (ok), RSI short (fail)
    assert sig2 == "neutral"
    assert val2 == 1.0          # one satisfied


def test_composite_or_fires_when_any_match():
    comp = S.CompositeRule(
        "either",
        "ROC long OR RSI long",
        (S.Condition("ROC(30) momentum", "long"), S.Condition("RSI(14) 30/70", "long")),
        combine="OR",
        direction="long",
    )
    sig, val = comp(_rising())  # ROC long satisfied, RSI not
    assert sig == "long"
    assert val == 1.0           # exactly one satisfied

    # OR with neither satisfied -> neutral
    comp2 = S.CompositeRule(
        "neither",
        "ROC short OR RSI long",
        (S.Condition("ROC(30) momentum", "short"), S.Condition("RSI(14) 30/70", "long")),
        combine="OR",
        direction="long",
    )
    sig2, val2 = comp2(_rising())   # ROC is long not short (fail); RSI short not long (fail)
    assert sig2 == "neutral"
    assert val2 == 0.0


def test_composite_value_equals_count_satisfied():
    comp = S.CompositeRule(
        "count",
        "",
        (S.Condition("ROC(30) momentum", "long"),
         S.Condition("SMA(50) trend", "long"),
         S.Condition("RSI(14) 30/70", "long")),
        combine="OR",
        direction="long",
    )
    sig, val = comp(_rising())   # ROC long + SMA long satisfied, RSI not -> 2
    assert sig == "long"
    assert val == 2.0


def test_composite_slots_into_screen_and_groups_sorts():
    comp = S.CompositeRule(
        "UP momentum",
        "",
        (S.Condition("ROC(30) momentum", "long"), S.Condition("SMA(50) trend", "long")),
        combine="AND",
        direction="long",
    )
    rows = S.screen({"UP": _rising(), "DN": _falling()}, comp)
    sig = {r.symbol: r.signal for r in rows}
    assert sig["UP"] == "long"
    assert sig["DN"] == "neutral"
    assert rows[0].signal == "long"     # longs grouped first


def test_register_composite_and_rule_by_name():
    # base rule resolves from RULES
    base = S.rule_by_name("ROC(30) momentum")
    assert isinstance(base, S.ScreenRule)
    assert base.name == "ROC(30) momentum"

    # unknown -> None
    assert S.rule_by_name("does-not-exist") is None

    comp = S.CompositeRule(
        "regtest-composite",
        "",
        (S.Condition("ROC(30) momentum", "long"),),
        combine="AND",
        direction="long",
    )
    S.register_composite(comp)
    try:
        got = S.rule_by_name("regtest-composite")
        assert isinstance(got, S.CompositeRule)
        assert got.name == "regtest-composite"
        assert comp in S.composites()
    finally:
        S._COMPOSITES.pop("regtest-composite", None)


def test_composite_to_from_dict_inverse():
    comp = S.CompositeRule(
        "roundtrip",
        "a description",
        (S.Condition("ROC(30) momentum", "long"), S.Condition("RSI(14) 30/70", "short")),
        combine="OR",
        direction="short",
        long_low=True,
    )
    d = S.composite_to_dict(comp)
    assert isinstance(d, dict)
    back = S.composite_from_dict(d)
    assert back == comp


def test_composite_store_roundtrip_and_reregister(tmp_path):
    path = str(tmp_path / "composites.json")
    store = S.CompositeStore(path)
    comp = S.CompositeRule(
        "store-composite",
        "",
        (S.Condition("ROC(30) momentum", "long"), S.Condition("SMA(50) trend", "long")),
        combine="AND",
        direction="long",
    )
    try:
        store.add(comp)
        assert "store-composite" in store.names()

        # fresh store reloads + re-registers into the live registry
        S._COMPOSITES.pop("store-composite", None)
        store2 = S.CompositeStore(path)
        loaded = store2.load()
        assert any(c.name == "store-composite" for c in loaded)
        assert S.rule_by_name("store-composite") is not None
        assert isinstance(S.rule_by_name("store-composite"), S.CompositeRule)

        store2.remove("store-composite")
        assert "store-composite" not in store2.names()
    finally:
        S._COMPOSITES.pop("store-composite", None)


def test_composite_store_migrates_legacy_json_then_deletes_file(tmp_path):
    """One-time sweep: a legacy composites.json is imported into the app DB, then removed."""
    import json

    legacy = tmp_path / "composites.json"
    legacy.write_text(json.dumps([{
        "name": "legacy-composite",
        "description": "",
        "conditions": [{"rule": "ROC(30) momentum", "direction": "long"}],
        "combine": "AND",
        "direction": "long",
        "long_low": False,
    }]), encoding="utf-8")
    try:
        store = S.CompositeStore(str(legacy))
        assert "legacy-composite" in store.names()       # data survived the migration
        assert S.rule_by_name("legacy-composite") is not None   # ... and re-registered
        assert not legacy.exists()                       # legacy file deleted
        assert (tmp_path / "db" / "vike_trader_app.sqlite").exists()   # ... into the app DB
    finally:
        S._COMPOSITES.pop("legacy-composite", None)


# --- volume filter --------------------------------------------------------------------

def test_volume_filter_drops_low_keeps_high():
    closes = {
        "HI": _rising(),
        "LO": _rising(),
    }
    volumes = {
        "HI": [1000.0] * 60,
        "LO": [10.0] * 60,
    }
    rows = S.screen(closes, S._rule_roc(30), symbol_volumes=volumes, min_volume=100.0)
    syms = [r.symbol for r in rows]
    assert "HI" in syms
    assert "LO" not in syms


def test_volume_filter_noop_when_omitted():
    closes = {"A": _rising(), "B": _rising()}
    volumes = {"A": [10.0] * 60, "B": [10.0] * 60}
    # no min_volume -> nothing dropped
    rows = S.screen(closes, S._rule_roc(30))
    assert {r.symbol for r in rows} == {"A", "B"}
    # volumes given but min_volume 0 -> no-op
    rows2 = S.screen(closes, S._rule_roc(30), symbol_volumes=volumes, min_volume=0.0)
    assert {r.symbol for r in rows2} == {"A", "B"}
