"""Offscreen tests for the Chart space layout + Bots wiring on the main window."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.data.store import Store  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.bots_panel import BotsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bars(n=12):
    """Sample bars — same construction as test_studio_gui.py."""
    return [Bar(ts=i * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.0 + i)
            for i in range(n)]


def test_main_window_uses_bots_panel(app):
    win = MainWindow()
    assert isinstance(win.bots, BotsPanel)
    assert not hasattr(win, "equity")  # equity lives in Studio now, not the Chart space


def test_first_space_is_chart(app):
    win = MainWindow()
    assert win._RAIL_ITEMS[0][1] == "Chart"
    assert win._mode_tag.text() == "CHART"


def test_launch_bot_records_run_and_populates_price_chart(app):
    """Launch Bot: records a new Historic Run and puts bars on the price chart."""
    win = MainWindow()
    # Use an in-memory store so the test is self-contained and never pollutes storage/db/.
    win.store = Store(":memory:")

    bars = _bars()
    # load_bars runs the backtest and records (record=True by default)
    win.load_bars(bars)
    runs_before = len(win.store.list_runs())
    assert runs_before >= 1  # load_bars already saved once

    # _launch_bot re-runs the strategy on the already-loaded bars and saves another run.
    win._launch_bot()
    runs_after = len(win.store.list_runs())
    assert runs_after == runs_before + 1  # a new Historic Run was recorded

    # The price chart received the bars data.
    assert win.price._bars  # PriceChart._bars is set by set_data()

    win.close()
