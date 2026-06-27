import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.order_ticket import OrderTicket  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_ticket_mounted_and_parented(app, monkeypatch):
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        names = [t.objectName() for t in win.findChildren(QtWidgets.QToolBar)]
        assert "order_ticket_toolbar" in names
        assert win.order_ticket.parent() is not None
    finally:
        win.shutdown()


def test_send_disabled_when_not_armed(app, monkeypatch):
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        assert getattr(win, "_exec_session", None) is None
        assert win.order_ticket._send.isEnabled() is False
    finally:
        win.shutdown()


def test_set_armed_toggles_send(app):
    t = OrderTicket()
    assert t._send.isEnabled() is False
    t.set_armed(True, venue="binance", symbol="BTCUSDT", environment="DEMO")
    assert t._send.isEnabled() is True
    t.set_armed(False)
    assert t._send.isEnabled() is False


def test_submit_handler_inert_when_not_armed(app, monkeypatch):
    """Under VIKE_DISABLE_LIVE no session exists -> _on_submit_ticket must early-return with NO dialog
    and NO network. We assert it does not raise and does not flip into a 'sending' state."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        win._on_submit_ticket({"side": 1, "qty": 0.01, "order_type": "market",
                               "price": None, "reduce_only": False})
        # inert: no exception, status never advanced to 'sending…'
        assert win.order_ticket._status.text() != "sending…"
    finally:
        win.shutdown()


def test_armed_submit_reaches_hub_via_stub(app, monkeypatch):
    """With a FAKE armed session (a stub hub), a confirmed submit builds the OrderRequest from
    hub.venue/hub.symbol and reaches hub.submit_ticket — never the network. The confirm dialog is
    auto-accepted by stubbing _confirm_order (we never exec() a modal in headless)."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        captured = {}

        class _StubHub:
            venue = "okx"
            symbol = "BTC-USDT-SWAP"      # diverges from the chart symbol on purpose
            class _Acct:
                positions = {}
                def unrealized_pnl(self, *a, **k): return 0.0
            account = _Acct()
            bus = type("B", (), {"subscribe": lambda *a: None,
                                 "unsubscribe": lambda *a: None})()
            def submit_ticket(self, req):
                captured["req"] = req

        class _StubSession:
            hub = _StubHub()
            def shutdown(self): pass

        win._exec_session = _StubSession()
        monkeypatch.setattr(win, "_confirm_order", lambda req: True)   # no modal in headless

        win._on_submit_ticket({"side": -1, "qty": 0.02, "order_type": "limit",
                               "price": 65000.0, "reduce_only": True})

        req = captured["req"]
        assert req.venue == "okx"
        assert req.symbol == "BTC-USDT-SWAP"      # hub.symbol, NOT the chart 'BTCUSDT'
        assert req.side == -1
        assert req.qty == 0.02
        assert req.order_type == "limit"
        assert req.price == 65000.0
        assert req.reduce_only is True
        assert req.client_order_id                 # minted, non-empty
    finally:
        win._exec_session = None                   # avoid shutdown() touching the stub hub.shutdown
        win.shutdown()


def test_bus_subscribe_unsubscribe_lifecycle(app, monkeypatch):
    """Highest 0xC0000409 risk: a stale bus subscriber into a half-freed QLabel is the crash class.
    This test drives _on_arm_requested (not direct _exec_session assignment) so the subscribe-on-arm
    + unsubscribe-on-disarm + unsubscribe-on-shutdown paths all execute.
    The stub bus uses a real Python list so subscription membership is observable."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        subscribers = []

        class _StubBus:
            def subscribe(self, cb): subscribers.append(cb)
            def unsubscribe(self, cb):
                try:
                    subscribers.remove(cb)
                except ValueError:
                    pass

        class _StubHub:
            venue = "binance"
            symbol = "BTCUSDT"
            bus = _StubBus()
            registry = {}
            class _Acct:
                positions = {}
                marks = {}
                balance = 0.0
                realized_pnl = 0.0
                def unrealized_pnl(self, *a, **k): return 0.0
            account = _Acct()
            def shutdown(self): pass
            def submit_ticket(self, req): pass

        class _StubSession:
            hub = _StubHub()
            def shutdown(self): pass

        # Simulate arm: _on_arm_requested calls _maybe_start_live_exec first; we stub that so
        # _exec_session is set as if arming succeeded, then call the arm handler directly.
        stub_sess = _StubSession()
        monkeypatch.setattr(win, "_maybe_start_live_exec",
                            lambda spec=None: (setattr(win, "_exec_session", stub_sess) or True))
        from vike_trader_app.exec.arm_spec import ExecArmSpec
        spec = ExecArmSpec(venue="binance", product="spot", environment="DEMO", symbol="BTCUSDT", leverage=1)
        win._on_arm_requested(spec)

        # After arm: _on_exec_event must be subscribed
        assert win._on_exec_event in subscribers, "_on_exec_event not subscribed after arm"

        # After disarm: _on_exec_event must be removed (no stale ref into the widget)
        win._on_disarm_requested()
        assert win._on_exec_event not in subscribers, "_on_exec_event still subscribed after disarm"

        # Re-arm for the shutdown test
        stub_sess2 = _StubSession()
        stub_sess2.hub.bus = _StubBus()  # fresh bus with its own subscribers list
        subscribers2 = []
        stub_sess2.hub.bus.subscribe = lambda cb: subscribers2.append(cb)
        stub_sess2.hub.bus.unsubscribe = lambda cb: subscribers2.remove(cb) if cb in subscribers2 else None
        monkeypatch.setattr(win, "_maybe_start_live_exec",
                            lambda spec=None: (setattr(win, "_exec_session", stub_sess2) or True))
        win._on_arm_requested(spec)
        assert win._on_exec_event in subscribers2, "_on_exec_event not subscribed after re-arm"

        # Shutdown must also unsubscribe
        win.shutdown()
        assert win._on_exec_event not in subscribers2, "_on_exec_event still subscribed after shutdown"
    except Exception:
        # ensure shutdown runs even on failure so Qt objects are cleaned up
        try:
            win.shutdown()
        except Exception:
            pass
        raise


def test_confirm_cancel_blocks_submit(app, monkeypatch):
    """If the user cancels the confirm, submit_ticket is NOT reached."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        called = {"n": 0}

        class _StubHub:
            venue = "binance"; symbol = "BTCUSDT"
            class _Acct:
                positions = {}
                def unrealized_pnl(self, *a, **k): return 0.0
            account = _Acct()
            bus = type("B", (), {"subscribe": lambda *a: None, "unsubscribe": lambda *a: None})()
            def submit_ticket(self, req): called["n"] += 1

        class _StubSession:
            hub = _StubHub()
            def shutdown(self): pass

        win._exec_session = _StubSession()
        monkeypatch.setattr(win, "_confirm_order", lambda req: False)   # user cancels
        win._on_submit_ticket({"side": 1, "qty": 0.01, "order_type": "market",
                               "price": None, "reduce_only": False})
        assert called["n"] == 0
    finally:
        win._exec_session = None
        win.shutdown()


def test_exec_event_updates_position_label(app, monkeypatch):
    """_on_exec_event reads hub.account.positions keyed (venue, symbol, 'BOTH') and formats a
    position line. This test publishes a FillEvent through the stub hub and asserts the ticket's
    position label text contains the size and avg_px.
    Position value shape: {'size': float, 'avg_px': float} (verified: exec/accounting.py:26)."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        class _StubAcct:
            positions = {("binance", "BTCUSDT", "BOTH"): {"size": 0.01, "avg_px": 65000.0}}
            marks = {}
            balance = 0.0
            realized_pnl = 0.0
            def unrealized_pnl(self, venue, symbol, position_side="BOTH"): return 12.5

        class _StubHub:
            venue = "binance"
            symbol = "BTCUSDT"
            bus = type("B", (), {"subscribe": lambda *a: None, "unsubscribe": lambda *a: None})()
            registry = {}
            account = _StubAcct()
            def shutdown(self): pass

        class _StubSession:
            hub = _StubHub()
            def shutdown(self): pass

        win._exec_session = _StubSession()

        from vike_trader_app.exec.events import FillEvent, OrderFilled
        fe = FillEvent(trade_id="t1", client_order_id="c1", venue="binance",
                       symbol="BTCUSDT", side=1, last_qty=0.01, last_px=65000.0)
        filled = OrderFilled(client_order_id="c1", fill=fe)

        # Arm the status mapper so it doesn't filter the event
        win._ticket_status.arm("c1")
        win._on_exec_event(filled)

        pos_text = win.order_ticket._position.text()
        assert "0.01" in pos_text, f"size missing in position label: {pos_text!r}"
        assert "65000" in pos_text, f"avg_px missing in position label: {pos_text!r}"
    finally:
        win._exec_session = None
        win.shutdown()
