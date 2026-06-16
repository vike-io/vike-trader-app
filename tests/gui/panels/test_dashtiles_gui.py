"""Offscreen tests for the dashboard info tiles (Movers / P&L / Calendar / News headlines):
panel registration, fresh-run defaults, data rendering, news mirroring, session round-trip.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import time
from dataclasses import dataclass, field

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6QtAds")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.data.news.models import NewsItem  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


_TILE_KEYS = ("movers", "pnl", "ecal", "headlines")


def test_tiles_registered_as_panels(app):
    win = MainWindow(session_path=None)
    for key in _TILE_KEYS:
        assert key in win._panel_dock_map          # a real ADS dock
        assert key in win._panel_btns              # a rail toggle
        assert key in win._panel_visible
    win.close()


def test_tiles_default_closed_on_fresh_run(app):
    win = MainWindow(session_path=None)
    for key in _TILE_KEYS:
        assert win._panel_btns[key].isChecked() is False
    # chart-unify keystone: there is no central chart/backtester panel anymore; the remaining
    # side panels (market / trades) also default CLOSED on a fresh, empty workspace.
    assert win._panel_btns["market"].isChecked() is False
    assert win._panel_btns["trades"].isChecked() is False
    win.close()


def test_movers_tile_renders_and_ranks(app):
    win = MainWindow(session_path=None)
    win._movers_tile.merge_prices({
        "BTCUSDT": (62_000.0, 0.012),
        "ETHUSDT": (2_400.0, -0.05),
        "SOLUSDT": (150.0, 0.002),
    })
    rows = win._movers_tile._rows
    assert len(rows) == 3
    # biggest mover first: the first row's symbol label is ETHUSDT
    first_labels = rows[0].findChildren(QtWidgets.QLabel)
    assert first_labels and first_labels[0].text() == "ETHUSDT"
    # quote updates fold in (no duplicate rows for the same symbol)
    win._movers_tile.merge_prices({"BTCUSDT": (63_000.0, 0.08)})
    rows = win._movers_tile._rows
    assert len(rows) == 3
    assert rows[0].findChildren(QtWidgets.QLabel)[0].text() == "BTCUSDT"
    win.close()


def test_pnl_tile_follows_update_account(app):
    @dataclass
    class FakeResult:
        equity_curve: list = field(default_factory=lambda: [10_000.0, 10_400.0])
        final_equity: float = 10_400.0
        trades: list = field(default_factory=list)

    win = MainWindow(session_path=None)
    win._result = FakeResult()
    win._update_account()
    assert len(win._pnl_tile._rows) == 3            # equity / pnl / return cells
    win._result = None
    win._update_account()
    assert win._pnl_tile._rows == []                # cleared back to the empty state
    win.close()


def test_news_tile_mirrors_feed_without_starting_it(app):
    # News is an on-demand dock now: opening it wires its itemsUpdated -> headlines-tile mirror
    # (and, under the suite's VIKE_DISABLE_LIVE, does NOT arm the poller).
    win = MainWindow(session_path=None)
    win.open_tool("news")
    item = NewsItem(id="x", title="Bitcoin does a thing", url="http://e/x", summary="",
                    source="Wire", market="crypto",
                    published_ms=int(time.time() * 1000) - 120_000)
    win.news.on_items_received([item])              # feed delivers -> tile mirrors via signal
    rows = win._headlines_tile._rows
    assert len(rows) == 1
    labels = [lbl.text() for lbl in rows[0].findChildren(QtWidgets.QLabel)]
    assert "Bitcoin does a thing" in labels
    assert win.news._worker is None                 # mirroring never started the poller
    win.close()


def test_headlines_toggle_respects_live_kill_switch(app, monkeypatch):
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")    # suite default, but make it explicit
    win = MainWindow(session_path=None)
    win.open_tool("news")                           # the tile mirrors the News tool only while open
    win._on_headlines_toggled(True)
    assert win.news._worker is None                 # no network poller under the kill-switch
    win.close()


def test_calendar_tile_cache_only_never_raises(app):
    win = MainWindow(session_path=None)
    win._refresh_calendar_tile()                    # empty/missing cache -> placeholder, no crash
    assert win._ecal_tile._empty.isVisibleTo(win._ecal_tile) or win._ecal_tile._rows
    win.close()


def test_tile_visibility_persists_through_session(app, tmp_path):
    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    first._panel_btns["movers"].setChecked(True)    # open the movers tile
    first.close()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["panels"]["movers"] is True

    second = MainWindow(session_path=str(path))
    assert second._panel_btns["movers"].isChecked() is True
    assert second._panel_btns["pnl"].isChecked() is False   # untouched tile stays closed
    second.close()
