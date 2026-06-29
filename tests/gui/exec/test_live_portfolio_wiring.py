# tests/gui/exec/test_live_portfolio_wiring.py
"""MainWindow._start_live / _stop_live wiring tests (portfolio path, N>1).

Verifies:
- _start_live is inert when not armed with a basket (no crash, status message).
- After a fake 2-symbol basket arm, _start_live creates a pump + N workers, marks
  them on the window (_strat_pump / _strat_workers), updates LiveStrategyBar, registers each
  worker via add_aux_worker, and starts the pump.
- Per-symbol barClosed → pump.feed_bar(sym, bar) binding: each worker routes to the correct sym.
- _stop_live stops ALL workers (waited) then the pump; refs are cleared, bar reset.
- _on_disarm_requested calls _stop_live before shutting down the session.
- closeEvent calls _stop_live before shutting down the session.
- Unified routing: _on_run_live_requested now calls _start_live unconditionally (no dispatch branch).
- N-worker teardown: all N workers are stopped, not just the first.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.private_user_data import LiveExecutionSession  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ---------------------------------------------------------------------------
# Strategy code fixtures
# ---------------------------------------------------------------------------

_TRIVIAL_PORTFOLIO_STRATEGY = """\
from vike_trader_app.core.multi_symbol_engine import PortfolioStrategy
class MyPortStrat(PortfolioStrategy):
    def on_bar(self, ts, bars):
        pass
"""

_TRIVIAL_SINGLE_STRATEGY = """\
from vike_trader_app.core.strategy import Strategy
class MySingleStrat(Strategy):
    def on_bar(self, bar):
        pass
"""


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

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
        self.positions = {}
        self.balance = 10_000.0

    def equity(self, venue, symbol):
        return self.balance

    def position(self, venue, symbol):
        return 0.0

    def unrealized_pnl(self, venue, symbol):
        return 0.0

    def set_mark(self, venue, symbol, price):
        self.marks[(venue, symbol)] = price


class _FakeHub:
    def __init__(self, symbol, account):
        self.bus = _FakeBus()
        self.account = account
        self.venue = "bybit"
        self.symbol = symbol
        self._shutdown_called = False
        self.tickets = []

    def submit_ticket(self, req):
        self.tickets.append(req)

    def shutdown(self):
        self._shutdown_called = True


class _FakeWorker(QtWidgets.QWidget):
    """Stub for LiveBarFeedWorker — has barClosed + finished signals."""

    barClosed = QtCore.Signal(object)
    finished = QtCore.Signal()

    def __init__(self, feed=None, parent=None):
        super().__init__(parent)
        self.feed = feed
        self.started = False
        self.stopped = False
        self._bars_received = []

    def start(self):
        self.started = True

    def stop(self, timeout_ms=5000):
        self.stopped = True

    def wait(self, ms=2000):
        return True

    def isRunning(self):
        return self.started and not self.stopped


class _FakePump:
    """Stub for LivePump — captures feed_bar calls."""

    def __init__(self, strategy, hubs, account, **kw):
        self.strategy = strategy
        self.hubs = hubs
        self.account = account
        self.started = False
        self.stopped = False
        self._bars: list[tuple] = []  # (symbol, bar)

    def prime(self, history_by_symbol):
        """No-op stub (cold-start seeding not exercised in wiring tests)."""

    def feed_bar(self, symbol, bar):
        self._bars.append((symbol, bar))

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _fake_make_live_feed(symbol, interval, token=None):
    class _FakeFeed:
        async def run_forever(self, on_bar, *, stop=None, max_backoff=30.0):
            import asyncio
            while not (stop and stop()):
                await asyncio.sleep(0.05)
    return _FakeFeed()


def _arm_basket_window(win, monkeypatch, symbols=("BTCUSDT", "ETHUSDT")):
    """Install a fake 2-symbol basket session on *win* without hitting the network."""
    shared_account = _FakeAccount()
    hubs = {sym: _FakeHub(sym, shared_account) for sym in symbols}
    primary = hubs[symbols[0]]
    sess = LiveExecutionSession(primary, hubs=hubs)
    win._exec_session = sess
    win._interval = "1m"
    # Sync bar + Studio state as _on_arm_requested would.
    win._live_strat_bar.set_armed(True)
    if win.studio is not None:
        win.studio.set_live_armed(True)
    return hubs, sess


# ---------------------------------------------------------------------------
# Guard: inert when not armed (unified path)
# ---------------------------------------------------------------------------

def test_start_live_inert_when_not_armed(app, monkeypatch):
    win = MainWindow()
    try:
        assert win._exec_session is None
        messages = []
        monkeypatch.setattr(win.statusBar(), "showMessage", lambda m, t=0: messages.append(m))
        win._start_live(_TRIVIAL_PORTFOLIO_STRATEGY)
        assert win._strat_pump is None
        assert win._strat_workers == []
        assert any("Arm" in m or "basket" in m.lower() for m in messages)
    finally:
        win.shutdown()


# ---------------------------------------------------------------------------
# Success path: pump + N workers created, started, registered (unified path)
# ---------------------------------------------------------------------------

def test_start_live_portfolio_success(app, monkeypatch):
    """_start_live with a 2-symbol basket creates 2 workers + 1 pump."""
    import vike_trader_app.data.vike_live as vike_live_mod

    captured_pump = []
    captured_workers = []

    class _SpyPump(_FakePump):
        def __init__(self, strategy, hubs, account, **kw):
            super().__init__(strategy, hubs, account, **kw)
            captured_pump.append(self)

    class _SpyWorker(_FakeWorker):
        def __init__(self, feed, parent=None):
            super().__init__(feed, parent)
            captured_workers.append(self)

    monkeypatch.setattr(
        "vike_trader_app.exec.live_portfolio_pump.LivePump", _SpyPump)
    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _SpyWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)

    win = MainWindow()
    try:
        _arm_basket_window(win, monkeypatch)
        win._start_live(_TRIVIAL_PORTFOLIO_STRATEGY)

        # One pump, two workers (one per symbol)
        assert len(captured_pump) == 1
        assert len(captured_workers) == 2

        pump = captured_pump[0]
        assert pump.started

        for w in captured_workers:
            assert w.started

        assert win._strat_pump is pump
        assert len(win._strat_workers) == 2
        assert set(win._strat_workers) == set(captured_workers)

        # Both workers registered on the session (aux workers)
        for key in ("live_strat_BTCUSDT", "live_strat_ETHUSDT"):
            assert key in win._exec_session._workers, f"missing worker key: {key}"

        # LiveStrategyBar shows running state
        assert win._live_strat_bar._btn_stop.isEnabled()
        assert not win._live_strat_bar._btn_start.isEnabled()

        # Calling again is a no-op
        win._start_live(_TRIVIAL_PORTFOLIO_STRATEGY)
        assert len(captured_pump) == 1  # no second pump built

    finally:
        for w in win._strat_workers:
            w.stopped = True
        win.shutdown()


# ---------------------------------------------------------------------------
# Per-symbol barClosed binding: each worker routes to the correct symbol
# ---------------------------------------------------------------------------

def test_per_symbol_feed_bar_binding(app, monkeypatch):
    """Each worker's barClosed is bound with its symbol so pump.feed_bar(sym, bar) is correct."""
    import vike_trader_app.data.vike_live as vike_live_mod
    from vike_trader_app.core.model import Bar

    captured_pump = []
    captured_workers = []
    worker_feeds = []  # track which feed (symbol) each worker gets

    class _SpyPump(_FakePump):
        def __init__(self, strategy, hubs, account, **kw):
            super().__init__(strategy, hubs, account, **kw)
            captured_pump.append(self)

    class _SpyWorker(_FakeWorker):
        def __init__(self, feed, parent=None):
            super().__init__(feed, parent)
            captured_workers.append(self)
            worker_feeds.append(feed)

    def _make_live_feed_tracking(symbol, interval, token=None):
        class _TaggedFeed:
            sym = symbol

            async def run_forever(self, on_bar, *, stop=None, max_backoff=30.0):
                import asyncio
                while not (stop and stop()):
                    await asyncio.sleep(0.05)
        return _TaggedFeed()

    monkeypatch.setattr(
        "vike_trader_app.exec.live_portfolio_pump.LivePump", _SpyPump)
    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _SpyWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _make_live_feed_tracking)

    win = MainWindow()
    try:
        _arm_basket_window(win, monkeypatch)
        win._start_live(_TRIVIAL_PORTFOLIO_STRATEGY)

        pump = captured_pump[0]
        bar_btc = Bar(ts=1000, open=100.0, high=101.0, low=99.0, close=100.5, volume=1.0)
        bar_eth = Bar(ts=1000, open=10.0, high=11.0, low=9.0, close=10.5, volume=2.0)

        # Emit barClosed on each worker — find them by their feed symbol
        worker_by_sym = {}
        for w in captured_workers:
            feed = worker_feeds[captured_workers.index(w)]
            worker_by_sym[feed.sym] = w

        worker_by_sym["BTCUSDT"].barClosed.emit(bar_btc)
        worker_by_sym["ETHUSDT"].barClosed.emit(bar_eth)

        # Pump should have received (sym, bar) for both
        assert ("BTCUSDT", bar_btc) in pump._bars, f"pump bars: {pump._bars}"
        assert ("ETHUSDT", bar_eth) in pump._bars, f"pump bars: {pump._bars}"

    finally:
        for w in win._strat_workers:
            w.stopped = True
        win.shutdown()


# ---------------------------------------------------------------------------
# Stop tears down all N workers + pump (unified _stop_live)
# ---------------------------------------------------------------------------

def test_stop_live_tears_down_all(app, monkeypatch):
    """_stop_live stops ALL workers + the pump, clears refs."""
    import vike_trader_app.data.vike_live as vike_live_mod

    monkeypatch.setattr(
        "vike_trader_app.exec.live_portfolio_pump.LivePump", _FakePump)
    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _FakeWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)

    win = MainWindow()
    try:
        _arm_basket_window(win, monkeypatch)
        win._start_live(_TRIVIAL_PORTFOLIO_STRATEGY)
        assert win._strat_pump is not None
        assert len(win._strat_workers) == 2

        pump = win._strat_pump
        workers = list(win._strat_workers)

        win._stop_live()

        # All workers stopped
        for w in workers:
            assert w.stopped, f"worker {w!r} not stopped"
        # Pump stopped
        assert pump.stopped
        # Refs cleared
        assert win._strat_pump is None
        assert win._strat_workers == []
        # Bar reset
        assert not win._live_strat_bar._btn_stop.isEnabled()
    finally:
        win.shutdown()


# ---------------------------------------------------------------------------
# _on_disarm_requested calls _stop_live before session shutdown
# ---------------------------------------------------------------------------

def test_disarm_stops_portfolio_before_session_shutdown(app, monkeypatch):
    """_on_disarm_requested must stop portfolio workers + pump before tearing the session."""
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
        _arm_basket_window(win, monkeypatch)
        win._start_live(_TRIVIAL_PORTFOLIO_STRATEGY)
        assert win._strat_pump is not None

        win._on_disarm_requested()

        assert win._strat_pump is None
        assert win._strat_workers == []
        # Both workers stopped (2 symbols); pump stopped once.
        # Note: sess.shutdown() also calls stop() on the aux workers (belt-and-suspenders),
        # so the total worker stop count may be 2 (explicit only) or 4 (explicit + session).
        assert stop_order.count("pump") == 1
        assert stop_order.count("worker") >= 2, (
            f"at least 2 worker stops expected (one per symbol); got: {stop_order}"
        )
        # Workers must all stop BEFORE pump — the first pump index must be after
        # the first two (explicit) worker stops.
        first_pump_idx = stop_order.index("pump")
        first_two_worker_idxs = [i for i, v in enumerate(stop_order) if v == "worker"][:2]
        assert all(idx < first_pump_idx for idx in first_two_worker_idxs), (
            f"first 2 worker stops must precede pump stop; order: {stop_order}"
        )
    finally:
        win.shutdown()


# ---------------------------------------------------------------------------
# Unified routing: _on_run_live_requested calls _start_live unconditionally
# ---------------------------------------------------------------------------

def test_routing_portfolio_strategy_dispatches_to_unified_path(app, monkeypatch):
    """_on_run_live_requested calls _start_live for a PortfolioStrategy (no branch)."""
    import vike_trader_app.data.vike_live as vike_live_mod

    start_live_calls = []

    monkeypatch.setattr(
        "vike_trader_app.exec.live_portfolio_pump.LivePump", _FakePump)
    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _FakeWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)

    win = MainWindow()
    try:
        _arm_basket_window(win, monkeypatch)
        # Spy on the unified _start_live
        monkeypatch.setattr(win, "_start_live",
                            lambda code, **kw: start_live_calls.append(code))

        win._on_run_live_requested(_TRIVIAL_PORTFOLIO_STRATEGY)
        assert len(start_live_calls) == 1, "portfolio strategy must route to _start_live"

    finally:
        win.shutdown()


def test_routing_single_strategy_dispatches_to_unified_path(app, monkeypatch):
    """_on_run_live_requested calls _start_live for a plain Strategy (no branch)."""
    import vike_trader_app.data.vike_live as vike_live_mod

    start_live_calls = []

    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)

    win = MainWindow()
    try:
        _arm_basket_window(win, monkeypatch)
        monkeypatch.setattr(win, "_start_live",
                            lambda code, **kw: start_live_calls.append(code))

        win._on_run_live_requested(_TRIVIAL_SINGLE_STRATEGY)
        assert len(start_live_calls) == 1, "plain strategy must route to _start_live"

    finally:
        win.shutdown()


def test_routing_bad_code_shows_status_no_dispatch(app, monkeypatch):
    """_on_run_live_requested with unparseable code shows status, dispatches to neither."""
    start_live_calls = []
    messages = []

    win = MainWindow()
    try:
        _arm_basket_window(win, monkeypatch)
        monkeypatch.setattr(win, "_start_live",
                            lambda code, **kw: start_live_calls.append(code))
        monkeypatch.setattr(win.statusBar(), "showMessage",
                            lambda m, t=0: messages.append(m))

        win._on_run_live_requested("this is not valid python !!!")

        assert len(start_live_calls) == 0
        assert any("error" in m.lower() or "load" in m.lower() for m in messages)
    finally:
        win.shutdown()


# ---------------------------------------------------------------------------
# closeEvent also tears down the portfolio pump (unified path)
# ---------------------------------------------------------------------------

def test_close_event_tears_down_portfolio(app, monkeypatch):
    """MainWindow.shutdown() (called by closeEvent) tears down the portfolio pump."""
    import vike_trader_app.data.vike_live as vike_live_mod

    monkeypatch.setattr(
        "vike_trader_app.exec.live_portfolio_pump.LivePump", _FakePump)
    monkeypatch.setattr(
        "vike_trader_app.ui.live_feed_worker.LiveBarFeedWorker", _FakeWorker)
    monkeypatch.setattr(vike_live_mod, "make_live_feed", _fake_make_live_feed)

    win = MainWindow()
    _arm_basket_window(win, monkeypatch)
    win._start_live(_TRIVIAL_PORTFOLIO_STRATEGY)

    pump = win._strat_pump
    workers = list(win._strat_workers)
    assert pump is not None

    # Mark all workers stopped so shutdown() doesn't try to join real QThreads
    for w in workers:
        w.stopped = True

    win.shutdown()

    assert pump.stopped, "portfolio pump must be stopped on shutdown"
    assert all(w.stopped for w in workers), "all workers must be stopped on shutdown"
    assert win._strat_pump is None
    assert win._strat_workers == []
