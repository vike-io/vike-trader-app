import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.positions_panel import PositionsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _StubAcct:
    def __init__(self):
        self.positions = {("binance", "BTCUSDT", "BOTH"): {"size": 0.01, "avg_px": 64000.0}}
        self.marks = {("binance", "BTCUSDT"): 65000.0}
        self.balance = 1000.0
        self.realized_pnl = 5.0
    def unrealized_pnl(self, venue, symbol, position_side="BOTH"):
        pos = self.positions.get((venue, symbol, position_side))
        mark = self.marks.get((venue, symbol))
        if pos is None or mark is None:
            return 0.0
        return (mark - pos["avg_px"]) * pos["size"]


def _registry_with_open_order():
    from vike_trader_app.exec.events import OrderRequest
    from vike_trader_app.exec.order import ManagedOrder, OrderStatus
    req = OrderRequest(client_order_id="c1", venue="binance", symbol="BTCUSDT",
                       side=1, qty=0.02, order_type="limit", price=63000.0)
    return {"c1": ManagedOrder(request=req, status=OrderStatus.ACCEPTED)}


def _stub_hub():
    class _Bus:
        def subscribe(self, cb): pass
        def unsubscribe(self, cb): pass
    class _Hub:
        venue = "binance"; symbol = "BTCUSDT"
        bus = _Bus()
        account = _StubAcct()
        registry = _registry_with_open_order()
        def shutdown(self): pass
        def cancel_ticket(self, coid): _Hub.cancelled.append(coid)
    _Hub.cancelled = []
    return _Hub()


def test_panel_mounted_as_dock(app, monkeypatch):
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        assert "positions" in win._panel_dock_map
        assert win.positions_panel.parent() is not None
    finally:
        win.shutdown()


def test_panel_empty_when_not_armed(app, monkeypatch):
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        assert getattr(win, "_exec_session", None) is None
        assert win.positions_panel._pos.rowCount() == 0
        assert win.positions_panel._ord.rowCount() == 0
    finally:
        win.shutdown()


def test_set_rows_renders_positions_and_orders(app):
    from vike_trader_app.exec.positions_view import project_positions_orders
    hub = _stub_hub()
    p = PositionsPanel()
    p.set_armed(True)
    p.set_rows(project_positions_orders(hub.account, hub.registry, hub.venue))
    assert p._pos.rowCount() == 1
    assert p._ord.rowCount() == 1
    assert p._pos.item(0, 0).text() == "BTCUSDT"
    btn = p._ord.cellWidget(0, 7)  # Cancel column is index 7 (last of 8 columns)
    assert isinstance(btn, QtWidgets.QPushButton)
    assert btn.isEnabled() is True


def test_cancel_button_emits_coid(app):
    from vike_trader_app.exec.positions_view import project_positions_orders
    hub = _stub_hub()
    p = PositionsPanel()
    p.set_armed(True)
    p.set_rows(project_positions_orders(hub.account, hub.registry, hub.venue))
    captured = []
    p.cancelRequested.connect(captured.append)
    p._ord.cellWidget(0, 7).click()
    assert captured == ["c1"]


def test_set_armed_false_clears_rows(app):
    from vike_trader_app.exec.positions_view import project_positions_orders
    hub = _stub_hub()
    p = PositionsPanel()
    p.set_armed(True)
    p.set_rows(project_positions_orders(hub.account, hub.registry, hub.venue))
    assert p._ord.rowCount() == 1
    p.set_armed(False)
    assert p._ord.rowCount() == 0
    assert p._pos.rowCount() == 0


def test_exec_event_refreshes_panel(app, monkeypatch):
    """A published Order event flows through _on_exec_event and re-projects the panel from the hub."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        hub = _stub_hub()
        win._exec_session = type("S", (), {"hub": hub, "shutdown": lambda self: None})()
        win.positions_panel.set_armed(True)
        from vike_trader_app.exec.events import OrderAccepted
        win._on_exec_event(OrderAccepted(client_order_id="c1"))
        assert win.positions_panel._ord.rowCount() == 1
        assert win.positions_panel._pos.rowCount() == 1
    finally:
        win._exec_session = None
        win.shutdown()


def test_cancel_inert_when_not_armed(app, monkeypatch):
    """_on_cancel_ticket must early-return with NO dialog / NO network under VIKE_DISABLE_LIVE."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        assert getattr(win, "_exec_session", None) is None
        win._on_cancel_ticket("c1")   # must not raise, must not open a modal
    finally:
        win.shutdown()


def test_cancel_armed_confirm_reaches_hub(app, monkeypatch):
    """With a fake armed session + auto-accepted confirm, _on_cancel_ticket reaches hub.cancel_ticket."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        hub = _stub_hub()
        win._exec_session = type("S", (), {"hub": hub, "shutdown": lambda self: None})()
        monkeypatch.setattr(win, "_confirm_cancel", lambda coid: True)   # no modal in headless
        win._on_cancel_ticket("c1")
        assert hub.cancelled == ["c1"]
    finally:
        win._exec_session = None
        win.shutdown()


def test_cancel_confirm_declined_does_not_reach_hub(app, monkeypatch):
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        hub = _stub_hub()
        win._exec_session = type("S", (), {"hub": hub, "shutdown": lambda self: None})()
        monkeypatch.setattr(win, "_confirm_cancel", lambda coid: False)
        win._on_cancel_ticket("c1")
        assert hub.cancelled == []
    finally:
        win._exec_session = None
        win.shutdown()


def test_cancel_venue_error_is_swallowed(app, monkeypatch):
    """A genuine venue error from cancel_ticket must be logged, not propagated into the GUI slot."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        from vike_trader_app.exec.crypto_client import VenueApiError
        class _Hub:
            venue = "binance"; symbol = "BTCUSDT"
            registry = _registry_with_open_order()
            def cancel_ticket(self, coid): raise VenueApiError(-1021, "timestamp")
        win._exec_session = type("S", (), {"hub": _Hub(), "shutdown": lambda self: None})()
        monkeypatch.setattr(win, "_confirm_cancel", lambda coid: True)
        win._on_cancel_ticket("c1")   # must NOT raise
    finally:
        win._exec_session = None
        win.shutdown()
