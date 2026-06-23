# tests/gui/exec/test_app_live_gating_gui.py
"""Live exec is gated: blank flags / VIKE_DISABLE_LIVE -> no session; flags+creds -> session built
and joined at shutdown."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_blank_flags_means_no_session(app, monkeypatch):
    monkeypatch.delenv("VIKE_EXEC_VENUE", raising=False)
    monkeypatch.delenv("VIKE_EXEC_ENV", raising=False)
    win = MainWindow()
    try:
        assert win._maybe_start_live_exec() is False
        assert getattr(win, "_exec_session", None) is None
    finally:
        win.shutdown()


def test_disable_live_overrides_flags(app, monkeypatch):
    monkeypatch.setenv("VIKE_EXEC_VENUE", "binance")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("BINANCE_DEMO_API_KEY", "K")
    monkeypatch.setenv("BINANCE_DEMO_API_SECRET", "S")
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        assert win._maybe_start_live_exec() is False
    finally:
        win.shutdown()


def test_shutdown_joins_session_when_present(app, monkeypatch):
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)
    win = MainWindow()
    closed = {"v": False}

    class _FakeSession:
        def shutdown(self):
            closed["v"] = True

    win._exec_session = _FakeSession()
    win.shutdown()
    assert closed["v"] is True
