"""Richer tearsheet: per-symbol attribution + monthly returns (needs timestamps)."""

import pytest

from vike_trader_app.analysis.tearsheet import monthly_returns, write_tearsheet_html
from vike_trader_app.core.engine import Result
from vike_trader_app.core.model import Bar
from vike_trader_app.core.portfolio import PortfolioEngine, PortfolioStrategy
from vike_trader_app.core.model import Trade

MONTH = 30 * 24 * 60 * 60 * 1000  # ~1 month in ms


def test_monthly_returns_buckets_by_calendar_month():
    # two points in Jan 2025, two in Feb 2025 (epoch ms)
    jan = 1_735_732_800_000  # 2025-01-01
    feb = 1_738_411_200_000  # 2025-02-01
    ts = [jan, jan + 86_400_000, feb, feb + 86_400_000]
    eq = [10_000.0, 11_000.0, 11_000.0, 12_100.0]
    mr = monthly_returns(ts, eq)
    labels = [m[0] for m in mr]
    assert labels == ["2025-01", "2025-02"]
    assert mr[0][1] == pytest.approx(0.10)   # 10000 -> 11000
    assert mr[1][1] == pytest.approx(0.10)   # 11000 -> 12100


def test_tearsheet_renders_monthly_and_attribution(tmp_path):
    eq = [10_000.0, 10_500.0, 9_900.0, 10_120.0]
    ts = [1_735_732_800_000 + i * MONTH for i in range(4)]  # 4 consecutive months
    result = Result(trades=[], equity_curve=eq, final_equity=10_120.0)
    path = write_tearsheet_html(
        tmp_path / "r.html", result, title="X",
        timestamps=ts, attribution={"BTCUSDT": 150.0, "ETHUSDT": -30.0},
    )
    html = path.read_text()
    assert "Monthly" in html
    assert "2025-01" in html
    assert "Attribution" in html and "BTCUSDT" in html and "ETHUSDT" in html


class _SplitTrade(PortfolioStrategy):
    def on_bar(self, ts, bars):
        if self.index == 0:
            self.buy("WIN", 1.0)    # WIN rises -> profit
            self.buy("LOSE", 1.0)   # LOSE falls -> loss
        elif self.index == 2:
            self.close("WIN")
            self.close("LOSE")


def test_portfolio_per_symbol_attribution():
    def s(opens):
        return [Bar(ts=i * 60_000, open=o, high=o + 1, low=o - 1, close=o, volume=1.0) for i, o in enumerate(opens)]

    bars = {"WIN": s([100, 100, 120, 120]), "LOSE": s([100, 100, 80, 80])}
    res = PortfolioEngine(bars, _SplitTrade(), cash=100_000.0).run()
    assert res.per_symbol_pnl["WIN"] == pytest.approx(20.0)    # bought 100, closed 120
    assert res.per_symbol_pnl["LOSE"] == pytest.approx(-20.0)  # bought 100, closed 80
