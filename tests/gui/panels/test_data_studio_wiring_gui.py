"""Offscreen integration: Data tab Test buttons drive the Studio space on the main window.

The Data tab is an on-demand dock now (empty-workspace re-arch): each test opens it via
``win.open_tool("data")`` (which builds the DataManagerTab, mirrors it onto win.datamanager, and
wires its test_symbol_requested / test_dataset_requested signals) before driving those signals.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.data.datasets import DataSet  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bars(n=12):
    return [Bar(ts=i * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.0 + i)
            for i in range(n)]


def test_test_symbol_loads_studio_and_switches(app):
    win = MainWindow()
    win.open_tool("data")
    bars = _bars()
    win.datamanager.test_symbol_requested.emit("BTCUSDT", bars)
    assert win.studio._bars == bars
    assert win.tabs.currentWidget() is win.studio


def test_test_dataset_runs_portfolio_into_studio(app):
    from vike_trader_app.analysis.strategy_templates import TEMPLATES
    win = MainWindow()
    win.open_tool("data")
    win.studio.editor.setText(TEMPLATES[0].code)   # a known-good strategy
    a, b = _bars(60), _bars(60)
    win.datamanager.test_dataset_requested.emit(DataSet("DS", ["A", "B"], interval="1m"), {"A": a, "B": b})
    assert win.tabs.currentWidget() is win.studio
    assert win.studio.results.last_report is not None


def test_test_dynamic_dataset_runs_with_membership_ranges(app):
    # a dynamic DataSet (per-symbol DateRange membership) runs through the portfolio path without error
    from vike_trader_app.analysis.strategy_templates import TEMPLATES
    from vike_trader_app.data.datasets import DataSet, DateRange
    win = MainWindow()
    win.open_tool("data")
    win.studio.editor.setText(TEMPLATES[0].code)
    a, b = _bars(60), _bars(60)
    ds = DataSet("Dyn", ["A", "B"], interval="1m", ranges={"B": [DateRange(b[30].ts, None)]})  # B joins mid-run
    win.datamanager.test_dataset_requested.emit(ds, {"A": a, "B": b})
    assert win.tabs.currentWidget() is win.studio
    assert win.studio.results.last_report is not None


def test_test_dataset_with_benchmark_symbol_uses_bars_from_bars_by_symbol(app):
    """DataSet.benchmark set to a symbol present in bars_by_symbol → report.benchmark_label == that symbol."""
    from vike_trader_app.analysis.strategy_templates import TEMPLATES
    win = MainWindow()
    win.open_tool("data")
    win.studio.editor.setText(TEMPLATES[0].code)
    a = _bars(60)
    spy = _bars(60)
    # DataSet with benchmark="SPY"; SPY bars are in the bars_by_symbol dict
    ds = DataSet("DS", ["A"], interval="1m", benchmark="SPY")
    win.datamanager.test_dataset_requested.emit(ds, {"A": a, "SPY": spy})
    assert win.tabs.currentWidget() is win.studio
    assert win.studio.results.last_report is not None
    assert win.studio.results.last_report.benchmark_label == "SPY"


def test_test_dataset_benchmark_missing_from_cache_falls_back_gracefully(app, monkeypatch):
    """DataSet.benchmark set but symbol NOT in bars_by_symbol and cache load fails → run succeeds
    with equal-weight fallback (no crash)."""
    from vike_trader_app.analysis.strategy_templates import TEMPLATES
    win = MainWindow()
    win.open_tool("data")
    win.studio.editor.setText(TEMPLATES[0].code)
    a = _bars(60)
    # Monkeypatch read_series to always return [] so the cache load fails cleanly
    import vike_trader_app.data.parquet_source as ps
    monkeypatch.setattr(ps, "read_series", lambda *args, **kwargs: [])
    ds = DataSet("DS2", ["A"], interval="1m", benchmark="MISSING")
    win.datamanager.test_dataset_requested.emit(ds, {"A": a})
    # must not crash and must still produce a report
    assert win.studio.results.last_report is not None
    # fallback to equal-weight
    assert win.studio.results.last_report.benchmark_label == "Equal-weight buy & hold"
