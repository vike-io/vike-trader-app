import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.exec.arm_spec import ExecArmSpec  # noqa: E402
from vike_trader_app.ui.exec_arm import ExecArmBar  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_bar_builds_perp_spec_with_leverage(app):
    bar = ExecArmBar()
    bar.set_selection(venue="bybit", product="Perp", environment="DEMO", leverage=5)
    spec = bar.current_spec("BTCUSDT")
    assert spec == ExecArmSpec("bybit", "DEMO", "perp", "BTCUSDT", 5.0)


def test_leverage_spin_disabled_for_spot(app):
    bar = ExecArmBar()
    bar.set_selection(venue="binance", product="Spot", environment="DEMO", leverage=1)
    assert bar._leverage.isEnabled() is False


def test_arm_signal_routes_to_maybe_start_and_honors_disable_live(app, monkeypatch):
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")     # network-free
    win = MainWindow()
    try:
        spec = ExecArmSpec("binance", "DEMO", "perp", "BTCUSDT", 5.0)
        assert win._on_arm_requested(spec) is False   # disabled -> no session, no network
        assert getattr(win, "_exec_session", None) is None
    finally:
        win.shutdown()


def test_exec_arm_bar_is_mounted_in_main_window(app, monkeypatch):
    """5f: the arm bar must be MOUNTED (visible) in the MainWindow — a free-floating, unparented
    widget would leave live exec user-unreachable (the whole point of 5f-C). Confirm it lives in a
    top toolbar and is parented."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        toolbars = [t.objectName() for t in win.findChildren(QtWidgets.QToolBar)]
        assert "exec_arm_toolbar" in toolbars
        assert win.exec_arm.parent() is not None          # mounted, not free-floating
    finally:
        win.shutdown()


def test_disarm_stops_funding_timer_and_clears_pollers(app, monkeypatch):
    """After _on_disarm_requested: funding timer is stopped and pollers list is empty.
    Re-arming must be safe (timer restarts on next arm)."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        # Simulate a perp arm having populated funding infrastructure
        win._funding_timer.start(5000)
        win._funding_pollers = [object()]   # sentinel — any non-empty list

        win._on_disarm_requested()

        assert not win._funding_timer.isActive(), "_funding_timer must be stopped after disarm"
        assert win._funding_pollers == [], "_funding_pollers must be cleared after disarm"
        assert getattr(win, "_exec_session", None) is None
    finally:
        win.shutdown()


def test_restore_arm_selection_does_not_auto_arm(app, monkeypatch):
    """Restoring QSettings selection must NOT arm the session."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    from PySide6 import QtCore
    s = QtCore.QSettings("vike", "trader")
    s.setValue("exec/venue", "bybit")
    s.setValue("exec/product", "Perp")
    s.setValue("exec/environment", "DEMO")
    s.setValue("exec/leverage", 5)
    win = MainWindow()
    try:
        # After construction + _restore_arm_selection, no session should be armed
        assert getattr(win, "_exec_session", None) is None
        # The combo should reflect the saved selection
        assert win.exec_arm._venue.currentText() == "bybit"
    finally:
        win.shutdown()
        # Clean up the QSettings key to avoid polluting other tests
        s.remove("exec/venue")
        s.remove("exec/product")
        s.remove("exec/environment")
        s.remove("exec/leverage")


def test_leverage_spin_enabled_for_perp(app):
    bar = ExecArmBar()
    bar.set_selection(venue="okx", product="Perp", environment="DEMO", leverage=10)
    assert bar._leverage.isEnabled() is True
    assert bar._leverage.value() == 10


def test_arm_button_text_reflects_mainnet(app):
    bar = ExecArmBar()
    bar.set_selection(venue="binance", product="Spot", environment="MAINNET", leverage=1)
    assert "MAINNET" in bar._arm.text()


def test_persist_arm_selection_saves_non_secret(app, monkeypatch):
    """_persist_arm_selection writes venue/product/env/leverage — never api_key/secret."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        spec = ExecArmSpec("okx", "DEMO", "perp", "BTCUSDT", 3.0)
        win._persist_arm_selection(spec)
        from PySide6 import QtCore
        s = QtCore.QSettings("vike", "trader")
        assert s.value("exec/venue") == "okx"
        assert s.value("exec/product") == "perp"
        assert s.value("exec/environment") == "DEMO"
        assert float(s.value("exec/leverage")) == 3.0
        # Ensure no api keys stored
        s_keys = set(s.allKeys())
        assert not any("key" in k.lower() or "secret" in k.lower() for k in s_keys if "exec/" in k)
    finally:
        win.shutdown()
        from PySide6 import QtCore
        s = QtCore.QSettings("vike", "trader")
        for k in ("exec/venue", "exec/product", "exec/environment", "exec/leverage"):
            s.remove(k)
