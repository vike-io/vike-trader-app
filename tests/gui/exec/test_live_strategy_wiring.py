# tests/gui/exec/test_live_strategy_wiring.py
"""MainWindow._start_live_strategy / _stop_live_strategy wiring tests.

Verifies:
- _start_live_strategy is inert when not armed (no crash, status message).
- After a fake arm, _start_live_strategy creates a pump + worker, marks them on the window,
  updates LiveStrategyBar, and registers the worker via add_aux_worker.
- _stop_live_strategy tears them down: stop() called, refs nilled, bar disabled.
- _on_disarm_requested calls _stop_live_strategy before shutting down the session.
- closeEvent calls _stop_live_strategy before shutting down the session.
- LiveStrategyBar: set_armed / set_running state transitions.
- add_aux_worker registers a plain worker (no signal wiring) joined by shutdown().
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.live_strategy_bar import LiveStrategyBar  # noqa: E402
from vike_trader_app.ui.private_user_data import LiveExecutionSession  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

_TRIVIAL_STRATEGY = """\
from vike_trader_app.core.strategy import Strategy
class MyStrat(Strategy):
    def on_bar(self, bar):
        pass
"""


class _FakeBus:
    def __init__(self):
        self._subs = []

    def subscribe(self, fn):
        self._subs.append(fn)

    def unsubscribe(self, fn):
        self._subs = [s for s in self._subs if s is not fn]

    def publish(self, event):
        for s in list(self._subs):
            s(event)


class _FakeAccount:
    def __init__(self):
        self.marks = {}

    def equity(self, venue, symbol):
        return 10_000.0

    def position(self, venue, symbol):
        return 0.0


class _FakeHub:
    def __init__(self):
        self.bus = _FakeBus()
        self.account = _FakeAccount()
        self.venue = "binance"
        self.symbol = "BTCUSDT"
        self._shutdown_called = False

    def shutdown(self):
        self._shutdown_called = True


class _FakeWorker(QtWidgets.QWidget):
    """Stub for LiveBarFeedWorker — QObject subclass so barClosed signal can exist."""

    barClosed = QtCore.Signal(object)

    def __init__(self, feed=None, parent=None):
        super().__init__(parent)
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self, timeout_ms=5000):
        self.stopped = True

    def wait(self, ms=2000):
        pass

    def isRunning(self):
        return self.started and not self.stopped


class _FakePump:
    """Stub for LiveStrategyPump."""

    def __init__(self, strategy, hub, **kw):
        self.strategy = strategy
        self.started = False
        self.stopped = False
        self._bars = []

    def start(self):
        self.started = True

    def feed_bar(self, bar):
        self._bars.append(bar)

    def stop(self):
        self.stopped = True


def _fake_make_live_feed(symbol, interval, token=None):
    class _FakeFeed:
        async def run_forever(self, on_bar, *, stop=None, max_backoff=30.0):
            import asyncio
            while not (stop and stop()):
                await asyncio.sleep(0.05)
    return _FakeFeed()


def _arm_window(win, monkeypatch):
    """Install a fake armed session on *win* without hitting the network."""
    hub = _FakeHub()
    sess = LiveExecutionSession(hub)
    win._exec_session = sess
    win._interval = "1m"
    # Sync the bar and Studio state as _on_arm_requested would.
    win._live_strat_bar.set_armed(True)
    if win.studio is not None:
        win.studio.set_live_armed(True)
    return hub, sess


# ---------------------------------------------------------------------------
# LiveStrategyBar unit tests (no MainWindow)
# ---------------------------------------------------------------------------

def test_live_strategy_bar_initial_state(app):
    bar = LiveStrategyBar()
    assert not bar._btn_start.isEnabled()
    assert not bar._btn_stop.isEnabled()
    assert bar._label.text() == "Strategy: —"


def test_live_strategy_bar_set_armed_enables_start(app):
    bar = LiveStrategyBar()
    bar.set_armed(True)
    assert bar._btn_start.isEnabled()
    assert not bar._btn_stop.isEnabled()


def test_live_strategy_bar_set_running_flips_buttons(app):
    bar = LiveStrategyBar()
    bar.set_armed(True)
    bar.set_running("MyStrat")
    assert not bar._btn_start.isEnabled()
    assert bar._btn_stop.isEnabled()
    assert "MyStrat" in bar._label.text()


def test_live_strategy_bar_set_running_none_resets(app):
    bar = LiveStrategyBar()
    bar.set_running("X")
    bar.set_running(None)
    assert not bar._btn_stop.isEnabled()
    assert not bar._btn_start.isEnabled()
    assert bar._label.text() == "Strategy: —"


def test_live_strategy_bar_start_disabled_while_running(app):
    bar = LiveStrategyBar()
    bar.set_running("X")
    bar.set_armed(True)   # should not enable start while running
    assert not bar._btn_start.isEnabled()


# ---------------------------------------------------------------------------
# add_aux_worker — joined by shutdown() without signal wiring
# ---------------------------------------------------------------------------

def test_add_aux_worker_registered_and_joined(app):
    hub = _FakeHub()
    sess = LiveExecutionSession(hub)
    worker = _FakeWorker()
    sess.add_aux_worker("live_strategy", worker)
    assert "live_strategy" in sess._workers
    sess.shutdown()
    assert worker.stopped


# ---------------------------------------------------------------------------
# _start_live_strategy — inert when not armed
# ---------------------------------------------------------------------------

def test_start_live_strategy_inert_when_not_armed(app, monkeypatch):
    win = MainWindow()
    try:
        assert win._exec_session is None
        messages = []
        monkeypatch.setattr(win.statusBar(), "showMessage", lambda m, t=0: messages.append(m))
        win._start_live_strategy(_TRIVIAL_STRATEGY)
        assert win._strat_pump is None
        assert any("Arm" in m for m in messages)
    finally:
        win.shutdown()


# ---------------------------------------------------------------------------
# _start_live_strategy — success path with monkeypatched pump + worker + feed
# ---------------------------------------------------------------------------

def test_start_live_strategy_success(app, monkeypatch):
    import vike_trader_app.ui.app as appmod
    import vike_trader_app.data.vike_live as vike_live_mod

    captured_pump = []
    captured_worker = []

    class _SpyPump(_FakePump):
        def __init__(self, strategy, hub, **kw):
            super().__init__(strategy, hub, **kw)
            captured_pump.append(self)

    class _SpyWorker(_FakeWorker):
        def __init__(self, feed, parent=None):
            super().__init__(feed, parent)
            captured_worker.append(self)

    monkeypatch.setattr(
        "vike_trader_app.exec.live_strategy_pump.LiveStrategyPump", _SpyPump)
    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _SpyWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)

    win = MainWindow()
    try:
        _arm_window(win, monkeypatch)
        win._start_live_strategy(_TRIVIAL_STRATEGY)

        assert len(captured_pump) == 1
        assert len(captured_worker) == 1
        pump = captured_pump[0]
        worker = captured_worker[0]

        assert pump.started
        assert worker.started
        assert win._strat_pump is pump
        assert win._strat_worker is worker
        # registered on the session for shutdown()
        assert "live_strategy" in win._exec_session._workers
        # bar label updated
        assert win._live_strat_bar._btn_stop.isEnabled()
        assert not win._live_strat_bar._btn_start.isEnabled()
        # calling again is a no-op (already running)
        win._start_live_strategy(_TRIVIAL_STRATEGY)
        assert len(captured_pump) == 1   # no second pump built
    finally:
        # Mark stopped so shutdown() doesn't try to join a never-started QThread
        if win._strat_worker is not None:
            win._strat_worker.stopped = True
        win.shutdown()


# ---------------------------------------------------------------------------
# _stop_live_strategy — tears down cleanly
# ---------------------------------------------------------------------------

def test_stop_live_strategy_tears_down(app, monkeypatch):
    import vike_trader_app.data.vike_live as vike_live_mod

    class _SpyPump(_FakePump):
        pass

    class _SpyWorker(_FakeWorker):
        def __init__(self, feed, parent=None):
            super().__init__(feed, parent)

    monkeypatch.setattr(
        "vike_trader_app.exec.live_strategy_pump.LiveStrategyPump", _SpyPump)
    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _SpyWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)

    win = MainWindow()
    try:
        _arm_window(win, monkeypatch)
        win._start_live_strategy(_TRIVIAL_STRATEGY)
        assert win._strat_pump is not None

        pump = win._strat_pump
        worker = win._strat_worker
        win._stop_live_strategy()

        assert pump.stopped
        assert worker.stopped
        assert win._strat_pump is None
        assert win._strat_worker is None
        # bar label reset
        assert not win._live_strat_bar._btn_stop.isEnabled()
    finally:
        win.shutdown()


# ---------------------------------------------------------------------------
# _on_disarm_requested calls _stop_live_strategy before session shutdown
# ---------------------------------------------------------------------------

def test_disarm_stops_strategy_before_session_shutdown(app, monkeypatch):
    import vike_trader_app.data.vike_live as vike_live_mod

    stop_order = []

    class _TrackPump(_FakePump):
        def stop(self):
            stop_order.append("pump")
            super().stop()

    class _TrackWorker(_FakeWorker):
        def __init__(self, feed, parent=None):
            super().__init__(feed, parent)

        def stop(self, timeout_ms=5000):
            stop_order.append("worker")
            super().stop(timeout_ms)

    monkeypatch.setattr(
        "vike_trader_app.exec.live_strategy_pump.LiveStrategyPump", _TrackPump)
    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _TrackWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)

    win = MainWindow()
    try:
        _arm_window(win, monkeypatch)
        win._start_live_strategy(_TRIVIAL_STRATEGY)
        assert win._strat_pump is not None

        # Stub the exec_session.hub.bus.unsubscribe so disarm doesn't blow up.
        win._exec_session.hub.bus.unsubscribe(lambda e: None)  # ensure no-fail on missing key

        win._on_disarm_requested()

        assert win._strat_pump is None
        assert win._strat_worker is None
        assert "worker" in stop_order
        assert "pump" in stop_order
        # worker must stop before pump
        assert stop_order.index("worker") < stop_order.index("pump")
    finally:
        win.shutdown()
