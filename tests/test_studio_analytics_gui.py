"""Offscreen tests for the three new analytics results tabs: Robustness, Monte Carlo, Periods."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.core.strategy import Strategy  # noqa: E402
from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner  # noqa: E402
from vike_trader_app.tester.config import TesterConfig  # noqa: E402
from vike_trader_app.ui.studio import StudioTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ~3 months of daily bars (90 bars) starting from 2024-01-01 (epoch ms)
_DAY_MS = 86_400_000
_T0 = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC in milliseconds


def _daily_bars(n=90, start_px=100.0, sym_seed=1):
    """Generate n daily bars with a mild uptrend; sym_seed shifts prices so symbols differ."""
    return [
        Bar(
            ts=_T0 + i * _DAY_MS,
            open=start_px + sym_seed * 0.1 + i * 0.05,
            high=start_px + sym_seed * 0.1 + i * 0.05 + 1.0,
            low=start_px + sym_seed * 0.1 + i * 0.05 - 1.0,
            close=start_px + sym_seed * 0.1 + i * 0.05 + 0.3,
            volume=1000.0,
        )
        for i in range(n)
    ]


class _BuyHold(Strategy):
    """Buy one unit on bar 0 and hold."""

    def on_bar(self, bar):
        if self.position.size == 0:
            self.buy(1.0)


def _portfolio_report(n_bars=90):
    bars = {
        "A": _daily_bars(n_bars, start_px=100.0, sym_seed=1),
        "B": _daily_bars(n_bars, start_px=50.0, sym_seed=2),
    }
    return MultiSymbolStrategyRunner(
        _BuyHold, bars, TesterConfig(cash=10_000.0)
    ).report()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_tab_strip_includes_new_analytics_tabs(app):
    """Tab strip must include Robustness, Monte Carlo, and Periods after the run."""
    tab = StudioTab()
    report = _portfolio_report()
    tab.show_portfolio_report(report, "TestDS")

    titles = [tab.results._tabs.tabText(i) for i in range(tab.results._tabs.count())]
    assert "Robustness" in titles
    assert "Monte Carlo" in titles
    assert "Periods" in titles


def test_monte_carlo_table_populated_for_report_with_trades(app):
    """Monte Carlo tab must have > 0 rows when the report has trades."""
    tab = StudioTab()
    report = _portfolio_report()
    tab.show_portfolio_report(report, "TestDS")

    # If there are trades, the MC table has metric rows
    if report.n_trades > 0:
        assert tab.results._mc_table.rowCount() > 0
        # Verify expected row labels
        labels = [
            tab.results._mc_table.item(r, 0).text()
            for r in range(tab.results._mc_table.rowCount())
        ]
        assert any("Terminal" in lbl or "Drawdown" in lbl or "Prob" in lbl or "Ruin" in lbl
                   for lbl in labels)
    else:
        # No trades -> graceful single-row hint
        assert tab.results._mc_table.rowCount() == 1


def test_periods_table_populated_given_equity_ts(app):
    """Periods tab must have > 0 rows (years) when equity_ts is threaded through."""
    tab = StudioTab()
    report = _portfolio_report(n_bars=90)  # ~3 months
    tab.show_portfolio_report(report, "TestDS")

    assert report.equity_ts is not None and len(report.equity_ts) > 0, \
        "equity_ts must be populated by the portfolio run"

    # The months heatmap must have at least one year row
    assert tab.results._periods_table.rowCount() > 0


def test_single_symbol_run_periods_tab_graceful_no_crash(app):
    """Single-symbol run (no equity_ts) leaves Periods tab non-crashing with a hint row."""
    from vike_trader_app.analysis.strategy_templates import TEMPLATES
    from vike_trader_app.tester import StrategyTester, TesterConfig as TC

    bars = _daily_bars(90)
    cls_code = TEMPLATES[0].code
    from vike_trader_app.core.strategy_loader import load_strategy_from_string
    cls = load_strategy_from_string(cls_code, validate=True)
    report = StrategyTester(cls(), bars, TC(cash=10_000.0, taker_fee=0.0)).run()

    # Single-symbol report has no equity_ts
    assert getattr(report, "equity_ts", None) is None or report.equity_ts == []

    tab = StudioTab()
    # Must not raise
    tab.results.show_report(report, bars)

    # The Periods table should show at least a hint row (not completely empty)
    assert tab.results._periods_table.rowCount() >= 1


def test_robustness_tab_has_rows_after_run(app):
    """Robustness tab must have at least the PSR and Sharpe rows."""
    tab = StudioTab()
    report = _portfolio_report()
    tab.show_portfolio_report(report, "TestDS")

    assert tab.results._robust_table.rowCount() >= 2
    labels = [
        tab.results._robust_table.item(r, 0).text()
        for r in range(tab.results._robust_table.rowCount())
    ]
    assert any("PSR" in lbl or "Sharpe" in lbl for lbl in labels)


def test_clear_resets_analytics_tables(app):
    """clear() must reset all three analytics tables to 0 rows."""
    tab = StudioTab()
    report = _portfolio_report()
    tab.show_portfolio_report(report, "TestDS")
    tab.results.clear()

    assert tab.results._robust_table.rowCount() == 0
    assert tab.results._mc_table.rowCount() == 0
    assert tab.results._periods_table.rowCount() == 0
    assert tab.results._dd_table.rowCount() == 0
