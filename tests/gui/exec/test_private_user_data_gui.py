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
from vike_trader_app.ui.private_user_data import LiveExecutionSession, PrivateUserDataWorker  # noqa: E402


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
