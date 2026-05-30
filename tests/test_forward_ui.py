"""Offscreen smoke tests for the GUI 'Forward (paper)' mode wiring (no network)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.forward import ForwardTester  # noqa: E402
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
    ft = ForwardTester(symbol="BTCUSDT", interval="1m", strategy=_BuyFirst(), cash=10_000.0)
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
    ft = ForwardTester(symbol="X", interval="1m", strategy=_BuyFirst(), cash=10_000.0)
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
