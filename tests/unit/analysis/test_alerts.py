"""Unit tests for local watchlist alerts (store + evaluate)."""

from vike_trader_app.analysis import screener as S
from vike_trader_app.analysis.alerts import AlertRule, AlertStore, evaluate


def test_store_add_persist_remove(tmp_path):
    path = str(tmp_path / "alerts.json")
    s = AlertStore(path)
    s.add(AlertRule(symbol="EURUSD", rule="RSI(14) 30/70", direction="long"))
    s.add(AlertRule(symbol="BTCUSDT", rule="ROC(30) momentum", direction="any"))
    assert len(AlertStore(path).rules()) == 2     # persisted + reloaded
    s.remove(0)
    assert [r.symbol for r in AlertStore(path).rules()] == ["BTCUSDT"]


def test_corrupt_file_starts_clean(tmp_path):
    p = tmp_path / "alerts.json"
    p.write_text("garbage", encoding="utf-8")
    assert AlertStore(str(p)).rules() == []
    assert p.exists()   # user-authored: an unreadable legacy file is left in place


def test_legacy_json_migrates_into_db_then_file_deleted(tmp_path):
    """One-time sweep: a legacy alerts.json is imported into the app DB, then removed."""
    import json

    legacy = tmp_path / "alerts.json"
    legacy.write_text(json.dumps([
        {"symbol": "EURUSD", "rule": "RSI(14) 30/70", "direction": "long", "note": "n"},
    ]), encoding="utf-8")
    s = AlertStore(str(legacy))
    assert [r.symbol for r in s.rules()] == ["EURUSD"]   # data survived the migration
    assert not legacy.exists()                           # legacy file deleted
    assert (tmp_path / "db" / "vike_trader_app.sqlite").exists()   # ... into the app DB
    # the DB is the source of truth from now on: a fresh store still sees the rule
    assert [r.note for r in AlertStore(str(legacy)).rules()] == ["n"]


def test_evaluate_matches_direction():
    rising = list(range(1, 60))     # RSI -> overbought -> "short"
    falling = list(range(60, 1, -1))  # RSI -> oversold -> "long"
    rules = [
        AlertRule("UP", "RSI(14) 30/70", "short"),   # should trigger (rising is overbought)
        AlertRule("UP", "RSI(14) 30/70", "long"),    # should NOT trigger
        AlertRule("DN", "RSI(14) 30/70", "any"),     # any non-neutral -> triggers
        AlertRule("MISSING", "RSI(14) 30/70", "any"),  # no data -> not triggered
    ]
    hits = evaluate(rules, {"UP": rising, "DN": falling})
    assert hits[0].triggered is True and hits[0].signal == "short"
    assert hits[1].triggered is False
    assert hits[2].triggered is True and hits[2].signal == "long"
    assert hits[3].triggered is False


def test_evaluate_resolves_registered_composite():
    rising = [100 + i for i in range(60)]   # ROC long AND SMA-trend long
    comp = S.CompositeRule(
        "alert-composite",
        "",
        (S.Condition("ROC(30) momentum", "long"), S.Condition("SMA(50) trend", "long")),
        combine="AND",
        direction="long",
    )
    S.register_composite(comp)
    try:
        rules = [AlertRule("UP", "alert-composite", "long")]
        hits = evaluate(rules, {"UP": rising})
        assert hits[0].triggered is True
        assert hits[0].signal == "long"
        assert hits[0].value == 2.0    # both conditions satisfied
    finally:
        S._COMPOSITES.pop("alert-composite", None)
