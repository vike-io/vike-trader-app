# tests/gui/exec/test_private_user_data_gui.py
"""LiveExecutionSession marshals worker events to the main thread (the only bus.publish caller);
shutdown stop()+wait()s each worker; failed.emit scrubs secrets; idle worker still joins."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6 import QtWidgets, QtCore  # noqa: E402

from vike_trader_app.exec.accounting import Account  # noqa: E402
from vike_trader_app.exec.bus import EventBus  # noqa: E402
from vike_trader_app.exec.events import FillEvent  # noqa: E402
from vike_trader_app.exec.live_oms import LiveOmsHub  # noqa: E402
from vike_trader_app.exec.order import ManagedOrder, OrderStatus  # noqa: E402
from vike_trader_app.exec.events import OrderRequest  # noqa: E402
from vike_trader_app.exec.risk import RiskGate, RiskLimits  # noqa: E402
from vike_trader_app.ui.private_user_data import (  # noqa: E402
    LiveExecutionSession,
    PrivateUserDataWorker,
    _scrub,
)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _NoClient:
    def submit(self, request):
        pass

    def detach(self):
        pass


def _hub():
    return LiveOmsHub(bus=EventBus(), account=Account(), gate=RiskGate(RiskLimits()),
                      client=_NoClient(), venue="binance", symbol="BTCUSDT")


def test_report_marshals_to_main_thread_slot(app):
    import threading

    hub = _hub()
    hub.registry["s-0"] = ManagedOrder(
        request=OrderRequest(client_order_id="s-0", venue="binance", symbol="BTCUSDT",
                             side=+1, qty=1.0, order_type="limit", price=100.0),
        status=OrderStatus.ACCEPTED)
    session = LiveExecutionSession(hub)
    main_thread = threading.get_ident()
    seen_threads = []
    orig = session._on_report

    def _spy(event):
        seen_threads.append(threading.get_ident())
        orig(event)

    session._on_report = _spy

    def _run_core(emit, stop):
        emit(FillEvent(trade_id="t1", client_order_id="s-0", venue="binance",
                       symbol="BTCUSDT", side=+1, last_qty=1.0, last_px=100.0))

    worker = PrivateUserDataWorker(_run_core)
    session.add_worker("binance", worker)
    worker.start()
    worker.wait(2000)
    app.processEvents()   # deliver the queued report on the main thread
    assert seen_threads == [main_thread]
    assert hub.account.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 1.0
    session.shutdown()


def test_failed_scrubs_secret(app):
    scrubbed = []
    worker = PrivateUserDataWorker(lambda emit, stop: (_ for _ in ()).throw(
        RuntimeError("boom signature=deadbeef secret=topsecret")))
    worker.failed.connect(scrubbed.append)
    worker.start()
    worker.wait(2000)
    app.processEvents()
    assert scrubbed
    assert "deadbeef" not in scrubbed[0]
    assert "topsecret" not in scrubbed[0]


def test_scrub_redacts_bybit_sign_and_auth_frame_shapes():
    """Fix 3: _scrub must cover the Bybit `sign` token, the `:` separator, and a stringified
    auth frame's args — the shapes the docstring claims — while leaving benign text alone."""
    # Bare hex sign with `=` separator is redacted.
    assert "abcdef0123456789" not in _scrub("sign=abcdef0123456789")
    # A stringified Bybit auth frame: neither the api_key nor the sign survives.
    frame = '{"op": "auth", "args": ["MYKEY123", 1700000000000, "DEADBEEFsig"]}'
    scrubbed = _scrub(frame)
    assert "MYKEY123" not in scrubbed
    assert "DEADBEEFsig" not in scrubbed
    # `:` separator shape (e.g. logged dict-ish) is also covered.
    assert "topsecretvalue" not in _scrub('secret: topsecretvalue')
    # Benign message must NOT be mangled — the (?<![A-Za-z]) boundary keeps `design` from
    # matching the `sign` branch.
    assert _scrub("design=v2 connected ok") == "design=v2 connected ok"


def test_disable_live_starts_no_worker(app, monkeypatch):
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    session = LiveExecutionSession(_hub())
    started = session.add_worker_if_enabled("binance", PrivateUserDataWorker(lambda emit, stop: None))
    assert started is False
    session.shutdown()


def test_shutdown_joins_each_worker(app):
    hub = _hub()
    session = LiveExecutionSession(hub)

    def _idle(emit, stop):
        import time
        while not stop():
            time.sleep(0.01)

    w = PrivateUserDataWorker(_idle)
    session.add_worker("binance", w)
    w.start()
    session.shutdown()
    assert not w.isRunning()


def test_shutdown_extends_join_when_worker_slow_to_stop(app):
    """A worker that does NOT join in 2s (e.g. blocked in a sync listenKey REST create during a
    reconnect) must get an EXTENDED join window — it must never be abandoned alive into os._exit
    (the 0xC0000409 teardown class). Confirmed 5d follow-up."""
    session = LiveExecutionSession(_hub())

    class _SlowWorker:
        def __init__(self):
            self.stopped = False
            self.waits = []

        def stop(self):
            self.stopped = True

        def wait(self, ms):
            self.waits.append(ms)
            return len(self.waits) >= 2   # times out on the first 2s wait, joins on the extension

    w = _SlowWorker()
    session._workers["binance"] = w
    session.shutdown()
    assert w.stopped
    assert w.waits == [2000, 8000]   # extended the join window past the initial 2s, not abandoned


def test_shutdown_does_not_extend_when_worker_joins_promptly(app):
    """No extra wait when the worker joins within 2s (the common path is unchanged)."""
    session = LiveExecutionSession(_hub())

    class _FastWorker:
        def __init__(self):
            self.waits = []

        def stop(self):
            pass

        def wait(self, ms):
            self.waits.append(ms)
            return True   # joins on the first 2s wait

    w = _FastWorker()
    session._workers["binance"] = w
    session.shutdown()
    assert w.waits == [2000]   # no extension
