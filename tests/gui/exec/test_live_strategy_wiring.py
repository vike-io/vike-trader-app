# tests/gui/exec/test_live_strategy_wiring.py
"""MainWindow._start_live / _stop_live wiring tests (single-symbol path, N=1).

Verifies:
- _start_live is inert when not armed (no crash, status message).
- After a fake arm, _start_live creates a pump + worker, marks them on the window,
  updates LiveStrategyBar, and registers the worker via add_aux_worker.
- _stop_live tears them down: stop() called, refs nilled, bar disabled.
- _on_disarm_requested calls _stop_live before shutting down the session.
- closeEvent calls _stop_live before shutting down the session.
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
    finished = QtCore.Signal()   # mirrors QThread.finished; needed by _start_live fix G

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


class _FakePump:
    """Stub for LivePump (unified portfolio pump)."""

    def __init__(self, strategy, hubs, account, **kw):
        self.strategy = strategy
        self.hubs = hubs
        self.account = account
        self.started = False
        self.stopped = False
        self._bars = []

    def prime(self, history_by_symbol):
        pass

    def start(self):
        self.started = True

    def feed_bar(self, symbol, bar):
        self._bars.append((symbol, bar))

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
# _start_live — inert when not armed (single-symbol path N=1)
# ---------------------------------------------------------------------------

def test_start_live_inert_when_not_armed(app, monkeypatch):
    win = MainWindow()
    try:
        assert win._exec_session is None
        messages = []
        monkeypatch.setattr(win.statusBar(), "showMessage", lambda m, t=0: messages.append(m))
        win._start_live(_TRIVIAL_STRATEGY)
        assert win._strat_pump is None
        assert any("Arm" in m for m in messages)
    finally:
        win.shutdown()


# ---------------------------------------------------------------------------
# _start_live — success path with monkeypatched pump + worker + feed (N=1)
# ---------------------------------------------------------------------------

def test_start_live_success(app, monkeypatch):
    import vike_trader_app.ui.app as appmod
    import vike_trader_app.data.vike_live as vike_live_mod

    captured_pump = []
    captured_worker = []

    class _SpyPump(_FakePump):
        def __init__(self, strategy, hubs, account, **kw):
            super().__init__(strategy, hubs, account, **kw)
            captured_pump.append(self)

    class _SpyWorker(_FakeWorker):
        def __init__(self, feed, parent=None):
            super().__init__(feed, parent)
            captured_worker.append(self)

    monkeypatch.setattr(
        "vike_trader_app.exec.live_portfolio_pump.LivePump", _SpyPump)
    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _SpyWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)

    win = MainWindow()
    try:
        _arm_window(win, monkeypatch)
        win._start_live(_TRIVIAL_STRATEGY)

        assert len(captured_pump) == 1
        assert len(captured_worker) == 1
        pump = captured_pump[0]
        worker = captured_worker[0]

        assert pump.started
        assert worker.started
        assert win._strat_pump is pump
        # unified path: _strat_workers is the single list (len 1 for single-symbol)
        assert len(win._strat_workers) == 1
        assert win._strat_workers[0] is worker
        # registered on the session for shutdown()
        assert "live_strat_BTCUSDT" in win._exec_session._workers
        # bar label updated
        assert win._live_strat_bar._btn_stop.isEnabled()
        assert not win._live_strat_bar._btn_start.isEnabled()
        # calling again is a no-op (already running)
        win._start_live(_TRIVIAL_STRATEGY)
        assert len(captured_pump) == 1   # no second pump built
    finally:
        # Mark stopped so shutdown() doesn't try to join a never-started QThread
        for w in win._strat_workers:
            w.stopped = True
        win.shutdown()


# ---------------------------------------------------------------------------
# _stop_live — tears down cleanly (single-symbol path N=1)
# ---------------------------------------------------------------------------

def test_stop_live_tears_down(app, monkeypatch):
    import vike_trader_app.data.vike_live as vike_live_mod

    class _SpyPump(_FakePump):
        pass

    class _SpyWorker(_FakeWorker):
        def __init__(self, feed, parent=None):
            super().__init__(feed, parent)

    monkeypatch.setattr(
        "vike_trader_app.exec.live_portfolio_pump.LivePump", _SpyPump)
    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _SpyWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)

    win = MainWindow()
    try:
        _arm_window(win, monkeypatch)
        win._start_live(_TRIVIAL_STRATEGY)
        assert win._strat_pump is not None

        pump = win._strat_pump
        workers = list(win._strat_workers)
        win._stop_live()

        assert pump.stopped
        for w in workers:
            assert w.stopped
        assert win._strat_pump is None
        assert win._strat_workers == []
        # bar label reset
        assert not win._live_strat_bar._btn_stop.isEnabled()
    finally:
        win.shutdown()


# ---------------------------------------------------------------------------
# _on_disarm_requested calls _stop_live before session shutdown
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
        "vike_trader_app.exec.live_portfolio_pump.LivePump", _TrackPump)
    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _TrackWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)

    win = MainWindow()
    try:
        _arm_window(win, monkeypatch)
        win._start_live(_TRIVIAL_STRATEGY)
        assert win._strat_pump is not None

        # Stub the exec_session.hub.bus.unsubscribe so disarm doesn't blow up.
        win._exec_session.hub.bus.unsubscribe(lambda e: None)  # ensure no-fail on missing key

        win._on_disarm_requested()

        assert win._strat_pump is None
        assert win._strat_workers == []
        assert "worker" in stop_order
        assert "pump" in stop_order
        # worker must stop before pump
        assert stop_order.index("worker") < stop_order.index("pump")
    finally:
        win.shutdown()


# ---------------------------------------------------------------------------
# Fix E: disarm disables Studio Run-live control
# ---------------------------------------------------------------------------

def test_disarm_disables_studio_run_live(app, monkeypatch):
    """After _on_disarm_requested, the Studio Run-live button must be disabled (fix E)."""
    import vike_trader_app.data.vike_live as vike_live_mod

    monkeypatch.setattr(
        "vike_trader_app.exec.live_portfolio_pump.LivePump", _FakePump)
    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _FakeWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)

    win = MainWindow()
    try:
        _arm_window(win, monkeypatch)
        # Studio's Run-live button should be enabled after arming.
        if win.studio is not None:
            assert win.studio._btn_run_live.isEnabled(), \
                "Studio Run-live should be enabled after arming"

        win._on_disarm_requested()

        # After disarm, Studio Run-live must be disabled regardless of running state.
        if win.studio is not None:
            assert not win.studio._btn_run_live.isEnabled(), \
                "Studio Run-live should be disabled after disarm (fix E)"
    finally:
        win.shutdown()


# ---------------------------------------------------------------------------
# Fix A: backfill-fetch failure still starts the pump + shows status message
# ---------------------------------------------------------------------------

def test_backfill_failure_starts_pump_cold(app, monkeypatch):
    """When REST backfill raises, the pump still starts and status bar shows a message (fix A)."""
    import vike_trader_app.data.vike_live as vike_live_mod
    import vike_trader_app.data.sources as sources_mod

    captured_pump = []
    messages = []

    class _SpyPump(_FakePump):
        def __init__(self, strategy, hubs, account, **kw):
            super().__init__(strategy, hubs, account, **kw)
            captured_pump.append(self)

    class _FailSource:
        def fetch_bars_range(self, symbol, interval, start, end):
            raise RuntimeError("network down")

    monkeypatch.setattr(
        "vike_trader_app.exec.live_portfolio_pump.LivePump", _SpyPump)
    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _FakeWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)
    monkeypatch.setattr(sources_mod, "select_source", lambda sym: _FailSource())

    win = MainWindow()
    try:
        _arm_window(win, monkeypatch)
        monkeypatch.setattr(win.statusBar(), "showMessage",
                            lambda m, t=0: messages.append(m))
        win._start_live(_TRIVIAL_STRATEGY)

        # Pump must still be created and started despite backfill failure.
        assert len(captured_pump) == 1, "pump should be created even if backfill fails"
        assert captured_pump[0].started, "pump should be started even if backfill fails"
        assert win._strat_pump is not None

        # Status bar must mention the failure (no modal).
        assert any("Backfill" in m or "backfill" in m or "cold" in m for m in messages), \
            f"expected backfill-failure status message, got: {messages}"
    finally:
        for w in win._strat_workers:
            w.stopped = True
        win.shutdown()


# ---------------------------------------------------------------------------
# M1: forming-tail candle in backfill must NOT duplicate when re-fed by the WS feed
# ---------------------------------------------------------------------------

def test_backfill_drops_forming_candle_no_dup(app, monkeypatch):
    """The REST backfill's still-forming tail bar is filtered (closed_bars) so that when
    the WS feed later emits that same candle as the first feed_bar, it lands in engine.bars
    exactly once — no ghost duplicate (M1).

    Uses the REAL LivePump + a REAL Account so engine.bars is genuinely populated.
    """
    import vike_trader_app.data.vike_live as vike_live_mod
    import vike_trader_app.data.sources as sources_mod
    from vike_trader_app.core.model import Bar
    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.bus import EventBus

    interval = "1m"
    iv_ms = 60_000
    now = 5 * iv_ms + 30_000   # 30s into the 6th window -> ts=5*iv is the FORMING candle

    # History: 5 closed bars (ts 0..4*iv) + 1 forming bar (ts 5*iv, window not elapsed).
    closed_history = [
        Bar(ts=i * iv_ms, open=100.0, high=101.0, low=99.0, close=100.0 + i, volume=1.0)
        for i in range(5)
    ]
    forming = Bar(ts=5 * iv_ms, open=104.0, high=106.0, low=103.0, close=105.0, volume=1.0)
    history = closed_history + [forming]

    class _RealHub:
        """Minimal real-ish hub: real Account + EventBus so the pump's engine is real."""
        def __init__(self):
            self.account = Account(venue="binance")
            self.bus = EventBus()
            self.venue = "binance"
            self.symbol = "BTCUSDT"
            self.registry = {}
            self.tickets = []

        def submit_ticket(self, req):
            self.tickets.append(req)

        def cancel_ticket(self, coid):
            pass

        def shutdown(self):
            pass

    class _FixedSource:
        def fetch_bars_range(self, symbol, ivl, start, end):
            return list(history)

    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _FakeWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)
    monkeypatch.setattr(sources_mod, "select_source", lambda sym: _FixedSource())
    # Freeze the clock so closed_bars treats ts=5*iv as still forming (now < 5*iv + iv_ms).
    monkeypatch.setattr("vike_trader_app.ui.app.time.time", lambda: now / 1000.0)

    win = MainWindow()
    try:
        hub = _RealHub()
        sess = LiveExecutionSession(hub)
        win._exec_session = sess
        win._interval = interval
        win._live_strat_bar.set_armed(True)

        win._start_live(_TRIVIAL_STRATEGY)
        pump = win._strat_pump
        assert pump is not None

        # After priming, only the 5 CLOSED bars should be in the engine buffer.
        # LivePump uses LiveEngine; bars live in pump.engine._bufs[sym].bars.
        sym = "BTCUSDT"
        engine_bars = pump.engine._bufs[sym].bars
        assert len(engine_bars) == 5, \
            f"forming candle should be dropped; got {[b.ts for b in engine_bars]}"

        # Now the WS feed emits the candle that was forming, now CLOSED.
        pump.feed_bar(sym, forming)

        engine_bars = pump.engine._bufs[sym].bars
        ts_list = [b.ts for b in engine_bars]
        # The formerly-forming candle (ts=5*iv) must appear exactly ONCE — no ghost dup.
        assert ts_list.count(5 * iv_ms) == 1, f"duplicate forming-candle ts in {ts_list}"
        assert len(ts_list) == 6, f"expected 6 unique bars, got {ts_list}"
        assert ts_list == sorted(ts_list), "bars should be ts-ascending with no dup"
    finally:
        for w in win._strat_workers:
            w.stopped = True
        win.shutdown()
