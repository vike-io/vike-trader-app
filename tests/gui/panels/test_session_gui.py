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
    """Fresh start (no session file) = EMPTY workspace after the chart-unify keystone: no chart
    frames, no central chart (`win.price is None`), no spaces. The symbol/interval defaults still
    seed `_symbol`/`_interval` for the first chart the user opens."""
    win = MainWindow(session_path=str(tmp_path / "session.json"))
    assert win._symbol == "BTCUSDT"
    assert win._interval == "1m"
    assert win._session is None
    assert win.price is None              # no central chart; nothing focused
    assert win._chart_frames == []        # no auto-created chart on a fresh start
    assert win.tabs.count() == 0          # no Chart space (no spaces at all)
    assert win.tabs.currentIndex() == -1  # empty SpaceDeck -> no current index


def test_close_writes_session_snapshot(app, tmp_path):
    path = tmp_path / "session.json"
    win = MainWindow(session_path=str(path))
    win.store = Store(":memory:")
    win._symbol, win._interval = "ETHUSDT", "1h"
    win.tabs.setCurrentIndex(0)              # Chart is the only space now
    win.open_tool("studio")                  # Studio opens as a window -> persisted via tool_windows
    win._panel_btns["market"].setChecked(True)
    win.close()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["symbol"] == "ETHUSDT"
    assert saved["interval"] == "1h"
    assert saved["space"] == 0
    assert any(s.get("key") == "studio" for s in saved["tool_windows"])  # open Studio window remembered
    assert saved["panels"]["market"] is True
    assert saved["geometry_hex"]              # non-empty opaque blob


def test_relaunch_restores_symbol_chart_frames_and_panels(app, tmp_path):
    """Chart-unify keystone: there is no central chart/space to restore — open chart WINDOWS
    persist as `session.documents` and reopen as ChartWindowFrame peers. The saved symbol/interval,
    the Studio dock, and panel toggles still restore."""
    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    first.store = Store(":memory:")
    first._symbol, first._interval = "ETHUSDT", "4h"
    first._new_chart_document("ETHUSDT", "4h", network=False, make_current=True)  # -> documents
    first.open_tool("studio")                # Studio dock -> restored via tool_windows
    first._panel_btns["trades"].setChecked(True)
    first.close()

    second = MainWindow(session_path=str(path))
    assert second._symbol == "ETHUSDT"
    assert second._interval == "4h"
    # the chart window reopened as a frame from session.documents (no central chart/space)
    assert len(second._chart_frames) == 1
    restored = second._chart_frames[0].doc
    assert (restored.symbol, restored.interval) == ("ETHUSDT", "4h")
    assert second.studio is not None         # the Studio dock was rebuilt on restore
    assert second._panel_btns["trades"].isChecked()
    assert second._panel_btns["market"].isChecked() is False  # untouched -> fresh default
    assert second._restored_geometry
    second.close()


def test_indicators_survive_relaunch_via_chart_document(app, tmp_path, monkeypatch):
    """User indicators (with params) re-attach after relaunch — chart-unify keystone: indicators
    now persist PER chart document (`session.documents[i]["indicators"]`), NOT via the removed
    central `chart_indicators` field. A reopened ChartDocument re-attaches its own saved indicators.

    Hermetic: stub the document's cache-first loader so the restored doc always gets synthetic
    bars (add_indicator/apply_indicator_states no-op on an empty chart); the hydration path is real.
    """
    import vike_trader_app.ui.chartdoc as chartdoc_mod
    from vike_trader_app.ui.dataload import LoadResult

    monkeypatch.setattr(chartdoc_mod, "load_symbol_bars",
                        lambda *a, **k: LoadResult(_bars()))

    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    first.store = Store(":memory:")
    doc = first._new_chart_document("BTCUSDT", "1h", network=False, make_current=True)
    assert doc.chart._bars                   # stubbed loader put bars on the chart
    ind = doc.chart.add_indicator("rsi", params={"period": 21})
    assert ind is not None
    first.close()

    # The indicator is serialized into the document's OWN state and RESTORED from there. The legacy
    # `chart_indicators` field is no longer the restore path (it's never read back on v4 — the
    # v3->v4 migration drops it, and there is no central chart to re-apply it to); restore is
    # per-document via session.documents.
    second = MainWindow(session_path=str(path))
    second.store = Store(":memory:")
    assert len(second._session.documents) == 1
    assert [s["name"] for s in second._session.documents[0]["indicators"]] == ["rsi"]

    # The reopened chart document (recreated in __init__ from session.documents) re-attached it.
    assert len(second._chart_frames) == 1
    chart = second._chart_frames[0].doc.chart
    names = [i.name for i in chart._indicators.values()]
    assert names == ["rsi"]
    restored = next(iter(chart._indicators.values()))
    assert restored.params == {"period": 21}
    # the Studio dock wasn't open in the saved session -> studio_price stays None (no chart to hydrate)
    assert second.studio_price is None
    second.close()


def test_session_disabled_when_path_none(app, tmp_path):
    win = MainWindow(session_path=None)
    win.store = Store(":memory:")
    win.close()  # must not write anywhere / must not raise
    assert win._session is None
