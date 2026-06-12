"""Offscreen tests for session save/restore wired through MainWindow.

These pass an EXPLICIT session_path (tmp dir) — the suite-wide VIKE_DISABLE_SESSION
kill-switch only disables the default storage/session.json path, so persistence is live here
while every other test stays session-free.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.data.store import Store  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bars(n=60):
    return [Bar(ts=i * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.0 + i)
            for i in range(n)]


def test_no_session_file_means_fresh_defaults(app, tmp_path):
    win = MainWindow(session_path=str(tmp_path / "session.json"))
    assert win._symbol == "BTCUSDT"
    assert win._interval == "1m"
    assert win.tabs.currentIndex() == 0
    assert win._session is None


def test_close_writes_session_snapshot(app, tmp_path):
    path = tmp_path / "session.json"
    win = MainWindow(session_path=str(path))
    win.store = Store(":memory:")
    win._symbol, win._interval = "ETHUSDT", "1h"
    win.tabs.setCurrentIndex(1)              # Studio (only Chart/Studio are spaces now)
    win._panel_btns["market"].setChecked(True)
    win.close()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["symbol"] == "ETHUSDT"
    assert saved["interval"] == "1h"
    assert saved["space"] == 1
    assert saved["panels"]["market"] is True
    assert saved["geometry_hex"]              # non-empty opaque blob


def test_relaunch_restores_symbol_space_and_panels(app, tmp_path):
    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    first.store = Store(":memory:")
    first._symbol, first._interval = "ETHUSDT", "4h"
    first.tabs.setCurrentIndex(1)             # Studio (only Chart/Studio are spaces now)
    first._panel_btns["trades"].setChecked(True)
    first.close()

    second = MainWindow(session_path=str(path))
    assert second._symbol == "ETHUSDT"
    assert second._interval == "4h"
    assert second.tabs.currentIndex() == 1
    assert second._panel_btns["trades"].isChecked()
    assert second._panel_btns["market"].isChecked() is False  # untouched -> fresh default
    assert second._restored_geometry
    second.close()


def test_indicators_survive_relaunch_via_startup_load(app, tmp_path, monkeypatch):
    """User indicators (with params) re-attach after relaunch once data loads."""
    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    first.store = Store(":memory:")
    first.load_bars(_bars(), record=False)
    ind = first.price.add_indicator("rsi", params={"period": 21})
    assert ind is not None
    first.close()

    second = MainWindow(session_path=str(path))
    second.store = Store(":memory:")
    assert [s["name"] for s in second._session.chart_indicators] == ["rsi"]

    # _startup_load -> _load_symbol (network/cache) -> apply indicators. Stub the load with
    # synthetic bars so the test is hermetic; the indicator hydration path stays real.
    monkeypatch.setattr(
        MainWindow, "_load_symbol",
        lambda self, symbol, interval=None: self.load_bars(_bars(), record=False),
    )
    second._startup_load()
    names = [i.name for i in second.price._indicators.values()]
    assert names == ["rsi"]
    restored = next(iter(second.price._indicators.values()))
    assert restored.params == {"period": 21}
    # the Studio chart had no user indicators -> none restored there
    assert not second.studio_price._indicators
    second.close()


def test_session_disabled_when_path_none(app, tmp_path):
    win = MainWindow(session_path=None)
    win.store = Store(":memory:")
    win.close()  # must not write anywhere / must not raise
    assert win._session is None
