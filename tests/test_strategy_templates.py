"""Every strategy template must be preflight-clean, loadable, and runnable through the tester."""

import math

from vike_trader_app.analysis.strategy_templates import TEMPLATES, StrategyTemplate
from vike_trader_app.core.model import Bar
from vike_trader_app.core.sandbox.preflight import check_strategy_source
from vike_trader_app.core.strategy_loader import load_strategy_from_string
from vike_trader_app.tester import StrategyTester, TesterConfig


def _bars(n=200):
    # trend + oscillation so the templates actually trade
    out = []
    prev = 100.0
    for i in range(n):
        p = 100.0 + 12.0 * math.sin(i / 9.0) + i * 0.05
        out.append(Bar(ts=i * 60_000, open=prev, high=max(p, prev) + 0.5,
                       low=min(p, prev) - 0.5, close=p, volume=1000.0))
        prev = p
    return out


def test_templates_nonempty():
    assert len(TEMPLATES) >= 5
    assert all(isinstance(t, StrategyTemplate) and t.code and t.name for t in TEMPLATES)


def test_every_template_is_preflight_clean():
    for t in TEMPLATES:
        assert check_strategy_source(t.code) == [], (t.name, check_strategy_source(t.code))


def test_every_template_loads_and_runs():
    bars = _bars()
    cfg = TesterConfig(taker_fee=0.0)
    for t in TEMPLATES:
        cls = load_strategy_from_string(t.code, validate=True)  # preflight + import
        report = StrategyTester(cls(), bars, cfg).run()          # must not raise
        assert report.n_trades >= 0                              # produces a valid report
