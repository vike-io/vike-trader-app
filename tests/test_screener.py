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
