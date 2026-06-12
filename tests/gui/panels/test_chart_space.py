"""Offscreen tests for the Chart space layout + Bots wiring on the main window."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.core.strategy import Strategy  # noqa: E402
from vike_trader_app.data.store import Store  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.bots_panel import BotsPanel  # noqa: E402


class _OverlayStrat(Strategy):
    """A strategy that draws one overlay line — to verify where auto-overlays land."""

    def on_bar(self, bar):  # noqa: ARG002
        pass

    def chart_overlays(self, closes):
        return {"MA": list(closes)}


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
    assert win._SPACE_ITEMS[0][1] == "Chart"
    assert win._mode_tag.text() == "CHART"


def test_feed_badge_shows_cached_not_live_when_no_feed_armed(app):
    """The feed badge is a connection watchdog. With VIKE_DISABLE_LIVE set (conftest does),
    no poller is ever armed — so even perfectly fresh cached bars must NOT paint '● LIVE';
    the badge reads '● CACHED · <provider>' instead (dim), keeping the watchdog honest."""
    import time as _time

    win = MainWindow()
    win.store = Store(":memory:")
    now = int(_time.time() * 1000)
    bars = [Bar(ts=now - (11 - i) * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i,
                close=100.0 + i) for i in range(12)]  # newest bar ≈ now → "fresh" by feed_health
    win.load_bars(bars, record=False)
    txt = win._feed_badge.text()
    assert "LIVE" not in txt, f"badge claims a live feed with no poller armed: {txt!r}"
    assert txt.startswith("● CACHED"), f"expected CACHED state, got: {txt!r}"


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


def test_chart_space_is_clean_no_auto_overlays(app):
    """The Chart space is a clean viewer: the default strategy's overlays go to the Studio/backtest
    chart only, never the Chart-space chart (indicators there come from the ƒx Indicators picker)."""
    win = MainWindow()
    win.store = Store(":memory:")
    win.load_bars(_bars(40), strategy_factory=_OverlayStrat)
    assert win.price._overlay_curves == {}            # Chart space: no auto strategy overlays
    assert "MA" in win.studio_price._overlay_curves    # Studio/backtest chart: keeps them
    win.close()
