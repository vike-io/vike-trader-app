"""Offscreen smoke tests for the GUI 'Forward (paper)' mode wiring (no network)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.paper import PaperTester  # noqa: E402
from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.core.strategy import Strategy  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bar(ts, c):
    return Bar(ts=ts, open=c, high=c + 1, low=c - 1, close=c, volume=1.0)


class _BuyFirst(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)


def test_render_forward_updates_charts_and_crumb(app):
    win = MainWindow()
    ft = PaperTester(symbol="BTCUSDT", interval="1m", strategy=_BuyFirst(), cash=10_000.0)
    win._forward = ft
    for i, c in enumerate((100.0, 110.0, 120.0)):
        ft.on_bar_live(_bar(i * 60_000, c))
    win._render_forward()
    assert "FORWARD" in win.crumb.text()
    assert len(win._fwd_bars) == 3  # live bars charted
    win._stop_forward()
    assert win._forward is None
    win.close()


def test_poll_tick_drains_a_fake_feed_into_the_tester(app):
    win = MainWindow()
    ft = PaperTester(symbol="X", interval="1m", strategy=_BuyFirst(), cash=10_000.0)
    win._forward = ft

    class _FakeFeed:
        poll_seconds = 1

        def __init__(self):
            self._done = False

        def poll_once(self):
            if self._done:
                return []
            self._done = True
            return [_bar(0, 100.0), _bar(60_000, 110.0)]

    win._feed = _FakeFeed()
    win._forward_poll_tick()
    assert len(ft.equity_curve) == 2
    win._stop_forward()
    win.close()


def test_forward_locks_backtest_controls(app):
    win = MainWindow()
    win._set_backtest_controls_enabled(False)
    assert not win.btn_play.isEnabled() and not win.btn_load.isEnabled()
    win._set_backtest_controls_enabled(True)
    assert win.btn_play.isEnabled()
    win.close()


def test_main_window_has_no_chart_space(app):
    """Chart-unify keystone: the docked central 'Chart' space is GONE. The SpaceDeck facade
    survives (tools/Studio still dock through it) but it hosts ZERO spaces, the icon rail's SPACE
    group is empty, and the central chart-space dock seam returns None."""
    from vike_trader_app.ui.dockshell import SpaceDeck

    win = MainWindow()
    assert isinstance(win.tabs, SpaceDeck)
    assert win.tabs.count() == 0                 # no Chart space (no spaces at all)
    assert win._SPACE_ITEMS == []
    assert win._chart_space_dock() is None
    # the icon rail's SPACE group mirrors the (now empty) space set one-for-one
    assert len(win._rail_group.buttons()) == win.tabs.count() == 0
    win.close()


def test_load_symbol_is_cache_first_when_fresh(app, monkeypatch):
    """Cache-first lives in the per-document load path now (chart-unify keystone). With no chart
    open the symbol box no-ops, so open a chart FRAME and assert ITS doc loads from a fresh cache
    WITHOUT touching the network. The doc loads via ui.dataload (Catalog + get_bars there)."""
    import time

    import vike_trader_app.data.catalog as cat_mod
    import vike_trader_app.ui.dataload as dataload_mod
    now = int(time.time() * 1000)
    base = now - 50 * 60_000
    fresh = [_bar(base + i * 60_000, 100.0) for i in range(50)]  # last bar = now-60s -> "fresh"

    class _Cat:
        root = "storage/parquet"

        def query(self, *a, **k):
            return fresh

        def symbols(self):           # used by _populate_watchlist at construction
            return []

    monkeypatch.setattr(cat_mod, "Catalog", _Cat)

    def _boom(*a, **k):
        raise AssertionError("get_bars (network) called despite a fresh cache")

    monkeypatch.setattr(dataload_mod, "get_bars", _boom)
    win = MainWindow()
    # open a chart window (network=True): a fresh cache must paint it WITHOUT a network fetch.
    doc = win._new_chart_document("EURUSD", "1m", network=True, make_current=True)
    assert doc.symbol == "EURUSD"
    assert len(doc._bars) == 50
    assert win.price is doc.chart        # the focused frame's chart tracks as self.price
    win.close()


def test_studio_agent_unconfigured_without_key(app, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    win = MainWindow()
    win.open_tool("studio")                  # Studio is an on-demand dock now -> build it first
    app.processEvents()
    assert win.studio._agent_client is None  # graceful no-AI default when no key
    win.close()
