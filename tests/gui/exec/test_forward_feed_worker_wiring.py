# tests/gui/exec/test_forward_feed_worker_wiring.py
"""Forward (paper) tester uses the orphan-safe LiveBarFeedWorker, not the deleted _LiveFeedWorker.

Slice 1a: _start_live_worker builds a LiveBarFeedWorker, routes barClosed -> _on_forward_bar and
failed -> _on_forward_failed; _stop_forward tears it down; the buggy _LiveFeedWorker is gone.
Drives a real MainWindow offscreen (project rule), with the worker + feed monkeypatched so no
network or real QThread is started.
"""

import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets  # noqa: E402

import vike_trader_app.ui.app as appmod  # noqa: E402
from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _SpyWorker(QtWidgets.QWidget):
    """Stub for LiveBarFeedWorker: real barClosed/failed signals; records start/stop. QWidget so it
    can host signals and be parented to nothing (no real QThread)."""

    barClosed = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(self, feed=None, parent=None):
        super().__init__(parent)
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self, timeout_ms=5000):
        self.stopped = True

    def wait(self, ms=2000):
        return True

    def isRunning(self):
        return self.started and not self.stopped


def _fake_make_live_feed(symbol, interval, token=None):
    class _FakeFeed:
        async def run_forever(self, on_bar, *, stop=None, max_backoff=30.0):
            import asyncio
            while not (stop and stop()):
                await asyncio.sleep(0.05)
    return _FakeFeed()


class _FakeForward:
    """Captures bars routed through _on_forward_bar -> on_bar_live; no real OmsHub."""

    def __init__(self):
        self.bars = []
        self.engine = types.SimpleNamespace(bars=[])
        self.equity_curve = []

    def on_bar_live(self, bar):
        self.bars.append(bar)

    def stop(self):
        pass


def _install(win, monkeypatch):
    """Patch the worker class, make_live_feed, the websockets probe, and silence side effects."""
    import vike_trader_app.data.vike_live as vike_live_mod
    monkeypatch.setitem(sys.modules, "websockets", types.ModuleType("websockets"))  # probe passes
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)
    monkeypatch.setattr("vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _SpyWorker)
    win._forward = _FakeForward()
    monkeypatch.setattr(win, "_render_forward", lambda: None)        # no chart repaint
    monkeypatch.setattr(win, "_arm_live_updates", lambda *a, **k: None)  # no live polling on stop


def test_live_feed_worker_class_removed():
    """The buggy _LiveFeedWorker must be deleted (replaced by LiveBarFeedWorker)."""
    assert not hasattr(appmod, "_LiveFeedWorker"), "_LiveFeedWorker should be gone in slice 1a"


def test_forward_uses_live_bar_feed_worker(app, monkeypatch):
    win = MainWindow()
    try:
        _install(win, monkeypatch)
        ok = win._start_live_worker("BTCUSDT", "1m")
        assert ok is True
        worker = win._fwd_worker
        assert isinstance(worker, _SpyWorker), "forward must build a LiveBarFeedWorker"
        assert worker.started
        bar = Bar(ts=1, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)
        worker.barClosed.emit(bar)
        assert win._forward.bars == [bar], "barClosed must route to _on_forward_bar"
    finally:
        win._stop_forward()
        win.shutdown()


def test_forward_failed_routes_and_stops(app, monkeypatch):
    warnings = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    win = MainWindow()
    try:
        _install(win, monkeypatch)
        win._start_live_worker("BTCUSDT", "1m")
        win._fwd_worker.failed.emit("feed exploded")
        assert warnings, "failed must surface a warning"
        assert win._fwd_worker is None, "_on_forward_failed must stop the forward worker"
    finally:
        win.shutdown()


def test_stop_forward_tears_down_worker(app, monkeypatch):
    win = MainWindow()
    try:
        _install(win, monkeypatch)
        win._start_live_worker("BTCUSDT", "1m")
        worker = win._fwd_worker
        win._stop_forward()
        assert worker.stopped, "_stop_forward must stop the worker"
        assert win._fwd_worker is None
    finally:
        win.shutdown()
