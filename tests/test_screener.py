"""Unit tests for the multi-symbol screener (pure logic)."""

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
