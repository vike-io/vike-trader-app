"""Offscreen tests for multi-instance chart documents (Phase 2): ChartDocument, LiveHub,
and the MainWindow wiring (open / current / close / session persist+restore).

load_symbol_bars is monkeypatched to synthetic bars so nothing hits the cache or network;
the live round-robin is disabled suite-wide (VIKE_DISABLE_LIVE in tests/conftest.py).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6QtAds")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

import vike_trader_app.ui.chartdoc as chartdoc  # noqa: E402
from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.chartdoc import ChartDocument, LiveHub  # noqa: E402
from vike_trader_app.ui.dataload import LoadResult  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bars(n=60, base=100.0):
    return [Bar(ts=i * 60_000, open=base + i, high=base + 1 + i, low=base - 1 + i,
                close=base + i) for i in range(n)]


def _focus_and_load(win, frame, symbol):
    """Focus ``frame`` then immediately drive the symbol box, with NO intervening processEvents().

    ``_active_chart_doc`` gates on the frame's ``isVisible()`` (already True after the frame's
    post-creation processEvents()). We must NOT pump events between setting the active frame and the
    load: under offscreen+xdist a stray activation from a sibling test's leaked window can otherwise
    steal focus mid-pump (flaky, not behavioral). Set-active + load are kept atomic here."""
    win._set_active_frame(frame)
    assert win._active_chart_doc() is frame.doc   # the frame is the symbol-box target
    win._load_symbol(symbol)


@pytest.fixture
def _synthetic_load(monkeypatch):
    """Make ChartDocument.load deterministic + offline (synthetic bars, no cache/network)."""
    monkeypatch.setattr(chartdoc, "load_symbol_bars",
                        lambda *a, **k: LoadResult(_bars()))


# --- ChartDocument --------------------------------------------------------------------------


def test_document_loads_own_symbol(app, _synthetic_load):
    doc = ChartDocument("ETHUSDT", "4h")
    assert doc.load()
    assert doc.symbol == "ETHUSDT" and doc.interval == "4h"
    assert doc.title() == "ETHUSDT · 4h"
    assert doc.chart._bars  # bars landed on the chart


def test_document_state_round_trip(app, _synthetic_load):
    doc = ChartDocument("BTCUSDT", "1h")
    doc.load()
    doc.chart.add_indicator("rsi", params={"period": 21})
    st = doc.state()
    assert st["symbol"] == "BTCUSDT" and st["interval"] == "1h"
    assert [i["name"] for i in st["indicators"]] == ["rsi"]

    clone = ChartDocument(st["symbol"], st["interval"])
    clone.load()
    clone.apply_state(st)
    assert [i.name for i in clone.chart._indicators.values()] == ["rsi"]


def test_document_merge_live_appends(app, _synthetic_load):
    doc = ChartDocument("BTCUSDT", "1m")
    doc.load()
    n0 = len(doc._bars)
    nxt = doc._bars[-1].ts + 60_000
    doc.merge_live([Bar(ts=nxt, open=200, high=201, low=199, close=200)])
    assert len(doc._bars) == n0 + 1


def test_ensure_loaded_requests_one_async_topup_then_no_ops(app, monkeypatch):
    """Async load: a cache-only (restore) load leaves the doc un-"loaded"; the first ensure_loaded
    kicks ONE off-thread top-up (request_topup), further calls no-op while it's pending, and once
    the worker lands (apply_topup) the doc is loaded and ensure_loaded stays a no-op."""
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(_bars()))
    monkeypatch.setattr("vike_trader_app.data.cache.append_series", lambda *a, **k: None)
    doc = ChartDocument("BTCUSDT", "1m"); hub = LiveHub(); hub.register(doc)
    requests = []
    monkeypatch.setattr(hub, "request_topup", lambda d, gen: requests.append(gen))
    doc.load(network=False)          # restore-style cache-only -> not loaded, no top-up
    assert doc._loaded is False and requests == []
    doc.ensure_loaded()              # first focus -> exactly one top-up requested
    assert len(requests) == 1 and doc._topup_pending is True
    doc.ensure_loaded()              # still pending -> NO duplicate request
    assert len(requests) == 1
    doc.apply_topup(doc._load_gen, _bars())   # the worker result lands -> loaded
    assert doc._loaded is True and doc._topup_pending is False
    doc.ensure_loaded()              # loaded -> no-op
    assert len(requests) == 1
    hub.shutdown()


def test_network_load_marks_loaded_when_topup_lands(app, monkeypatch):
    """A network load kicks an async top-up (not yet loaded); _loaded latches when apply_topup
    lands, after which ensure_loaded must NOT re-request."""
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(_bars()))
    monkeypatch.setattr("vike_trader_app.data.cache.append_series", lambda *a, **k: None)
    doc = ChartDocument("BTCUSDT", "1m"); hub = LiveHub(); hub.register(doc)
    requests = []
    monkeypatch.setattr(hub, "request_topup", lambda d, gen: requests.append(gen))
    doc.load()                       # stale cache -> top-up pending, not yet loaded
    assert doc._loaded is False and doc._topup_pending is True and len(requests) == 1
    doc.apply_topup(doc._load_gen, _bars())   # network top-up lands
    assert doc._loaded is True
    doc.ensure_loaded()              # loaded -> no re-request
    assert len(requests) == 1
    hub.shutdown()


def test_failed_async_topup_does_not_latch_loaded(app, monkeypatch):
    """A failed async top-up (bad symbol / offline) must NOT latch _loaded — so the doc isn't
    stuck empty and a later focus retries."""
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult([]))  # no cache
    doc = ChartDocument("BADSYM", "1m"); hub = LiveHub(); hub.register(doc)
    monkeypatch.setattr(hub, "request_topup", lambda d, gen: None)  # worker stubbed (DISABLE_LIVE)
    doc.load()                       # no cache -> top-up pending
    assert doc._loaded is False and doc._topup_pending is True
    doc.topup_failed(doc._load_gen, "offline")        # the fetch fails
    assert doc._loaded is False and doc._topup_pending is False    # not latched -> retry possible
    # a later successful top-up latches
    doc.load(); doc.apply_topup(doc._load_gen, _bars())
    assert doc._loaded is True
    hub.shutdown()


# --- LiveHub --------------------------------------------------------------------------------


def test_livehub_timer_disabled_under_env(app, _synthetic_load):
    # VIKE_DISABLE_LIVE is set by the suite conftest -> the round-robin timer never arms.
    hub = LiveHub()
    doc = ChartDocument("BTCUSDT", "1m")
    doc.load()
    hub.register(doc)
    assert not hub._timer.isActive()
    hub.unregister(doc)
    hub.shutdown()


def test_livehub_register_unregister(app, _synthetic_load):
    hub = LiveHub()
    d1, d2 = ChartDocument("BTCUSDT", "1m"), ChartDocument("ETHUSDT", "1m")
    hub.register(d1)
    hub.register(d2)
    assert d1 in hub._docs and d2 in hub._docs
    hub.unregister(d1)
    assert d1 not in hub._docs and d2 in hub._docs
    hub.shutdown()


# --- MainWindow wiring ----------------------------------------------------------------------


def test_new_chart_document_opens_floating_window(app, _synthetic_load):
    """S7: charts open as MC-style FLOATING WINDOWS over the workspace, not docked tabs."""
    win = MainWindow(session_path=None)
    assert win._chart_frames == []
    doc = win._new_chart_document("ETHUSDT", "1h")
    assert len(win._chart_frames) == 1
    frame = win._chart_frames[0]
    assert frame.doc is doc
    assert frame.parent() is win.dock_manager       # floats OVER the workspace
    assert doc in win._doc_widgets
    assert doc in win._live_hub._docs
    assert win._active_frame is frame
    win.close()


def test_open_in_new_chart_signal(app, _synthetic_load):
    win = MainWindow(session_path=None)
    win.watchlist.openInNewChart.emit("SOLUSDT")
    assert len(win._chart_frames) == 1
    assert win._doc_widgets[0].symbol == "SOLUSDT"
    win.close()


def test_chart_window_title_bar(app, _synthetic_load):
    """Every chart window has its own MC-style title bar: SYMBOL title + interval picker chip +
    pin/detach/min/max/close. The title text is the symbol only; the interval lives in the picker."""
    win = MainWindow(session_path=None)
    doc = win._new_chart_document("ETHUSDT", "2h")
    frame = win._chart_frames[0]
    assert frame._bar._title.text() == "ETHUSDT"        # title = symbol only (shared UnifiedTitleBar)
    assert frame._ivl_btn.text() == "2h"                # the interval shows in the picker chip
    doc.load("ETHUSDT", "4h")                       # picker follows the document's interval
    assert frame._bar._title.text() == "ETHUSDT"
    assert frame._ivl_btn.text() == "4h"
    win.close()


def test_clone_window_makes_independent_copy(app, _synthetic_load):
    """Stage 2: the ＋ clone button duplicates a window (same symbol/interval, new doc)."""
    win = MainWindow(session_path=None)
    win._new_chart_document("ETHUSDT", "2h")
    f0 = win._chart_frames[0]
    assert f0._bar._menu_cb is not None        # right-click menu wired
    win._clone_window(f0)
    assert len(win._chart_frames) == 2
    f1 = win._chart_frames[1]
    assert f1.doc is not f0.doc
    assert f1.doc.symbol == "ETHUSDT" and f1.doc.interval == "2h"
    win.close()


def test_attached_frame_snap_zones(app, _synthetic_load):
    """Stage 2: dragging an attached frame near a host edge/corner yields a half/quarter snap
    target; the centre yields no snap."""
    win = MainWindow(session_path=None)
    win.show()
    QtWidgets.QApplication.processEvents()
    win._new_chart_document("ETHUSDT", "1h")
    f = win._chart_frames[0]
    w, h = win.dock_manager.rect().width(), win.dock_manager.rect().height()
    left = f._snap_zone_rect(QtCore.QPoint(3, h // 2))
    assert left is not None and left.width() == w // 2 and left.height() == h
    corner = f._snap_zone_rect(QtCore.QPoint(3, 3))
    assert corner.width() == w // 2 and corner.height() == h // 2
    assert f._snap_zone_rect(QtCore.QPoint(w // 2, h // 2)) is None
    win.close()


def test_closing_window_unregisters(app, _synthetic_load):
    win = MainWindow(session_path=None)
    win._new_chart_document("ETHUSDT", "1h")
    win._chart_frames[0].close_window()
    app.processEvents()
    assert win._chart_frames == []
    assert win._doc_widgets == []
    assert win._live_hub._docs == []
    win.close()


def test_window_verbs_minimize_max_arrange(app, _synthetic_load):
    win = MainWindow(session_path=None)
    win.show()
    QtWidgets.QApplication.processEvents()
    win._new_chart_document("ETHUSDT", "1h")
    win._new_chart_document("SOLUSDT", "1h")
    win._new_chart_document("ADAUSDT", "1h")
    f1, f2, f3 = win._chart_frames
    f1.toggle_rollup()                              # minimize -> hide + park a tab on the left rail
    QtWidgets.QApplication.processEvents()
    assert not f1.isVisible()                       # hidden (not destroyed; still tracked)
    assert win._min_rail.has(f"chartwin:{id(f1)}")  # parked on the custom left rail
    f2.toggle_max()
    assert f2.size() == win.dock_manager.size()     # maximize fills the workspace
    f2.toggle_max()
    win._arrange_chart_windows("grid")              # tiles only the VISIBLE windows (f2, f3)
    geos = [f.geometry() for f in win._chart_frames if f.isVisible()]
    assert len(geos) == 2 and geos[0] != geos[1] and not geos[0].intersects(geos[1])
    win.close()


def test_maximized_window_refits_on_workspace_resize(app, _synthetic_load):
    """Regression: a chart window maximized at one workspace size must KEEP filling the workspace
    when the main window grows/shrinks (OS-maximize, drag). host_resized() handled this but was
    never wired to MainWindow.resizeEvent, so a window maximized small left empty space when the
    window grew (the reported 'chart not resized to full screen' bug)."""
    win = MainWindow(session_path=None)
    win.resize(900, 700)
    win.show()
    QtWidgets.QApplication.processEvents()
    win._new_chart_document("BTCUSDT", "1h")
    QtWidgets.QApplication.processEvents()
    frame = win._chart_frames[-1]
    frame.toggle_max()
    QtWidgets.QApplication.processEvents()
    assert frame.width() == win.dock_manager.rect().width()      # fills at the small size

    win.resize(1400, 900)                                        # grow the workspace
    QtWidgets.QApplication.processEvents()
    assert frame.width() == win.dock_manager.rect().width()      # still fills — no empty space
    win.resize(1100, 800)                                        # shrink
    QtWidgets.QApplication.processEvents()
    assert frame.width() == win.dock_manager.rect().width()
    win.close()


def test_open_windows_auto_retile_to_fill_on_resize(app, _synthetic_load):
    """Tiled workspace ('auto re-tile to fill' — the user's choice): with 2+ open floating windows,
    a workspace resize re-tiles them to fill it (no empty gap). A resize ARMS a debounced re-tile;
    a maximized window is left filling (not re-tiled); cascade opts out entirely."""
    win = MainWindow(session_path=None)
    win.resize(1200, 820)
    win.show()
    QtWidgets.QApplication.processEvents()
    win._new_chart_document("BTCUSDT", "1h")
    win._new_chart_document("ETHUSDT", "1h")
    win._arrange_chart_windows("grid")
    QtWidgets.QApplication.processEvents()
    host = win.dock_manager

    # BEHAVIOR: the re-tile (what the debounced timer fires) fills the current workspace — no gap.
    win._retile_open_windows()
    QtWidgets.QApplication.processEvents()
    right = max(f.geometry().right() for f in win._chart_frames)
    bottom = max(f.geometry().bottom() for f in win._chart_frames)
    assert right >= host.rect().width() - 10
    assert bottom >= host.rect().height() - 10

    # WIRING: a non-cascade resize ARMS the debounced re-tile; cascade does NOT. Drive resizeEvent
    # directly — synchronous, so no event-loop time passes and the 140ms singleShot can't fire/clear
    # mid-assert (an isActive() check after win.resize()+processEvents() is timing-flaky under xdist).
    win._retile_timer.stop()
    win.resizeEvent(QtGui.QResizeEvent(QtCore.QSize(1100, 800), QtCore.QSize(1200, 820)))
    assert win._retile_timer.isActive()
    win._arrange_chart_windows("cascade")
    win._retile_timer.stop()
    win.resizeEvent(QtGui.QResizeEvent(QtCore.QSize(1000, 760), QtCore.QSize(1100, 800)))
    assert not win._retile_timer.isActive()

    # GUARD: a maximized window is left filling — the re-tile is a no-op while one is maximized.
    win._arrange_chart_windows("grid")
    f0 = win._chart_frames[0]
    f0.toggle_max()
    QtWidgets.QApplication.processEvents()
    win._retile_open_windows()
    QtWidgets.QApplication.processEvents()
    assert f0._maxed and f0.width() == host.rect().width()
    f0.toggle_max()

    # GUARD: cascade re-tile is a no-op (free overlap preserved).
    win._arrange_chart_windows("cascade")
    geos = [QtCore.QRect(f.geometry()) for f in win._chart_frames]
    win._retile_open_windows()
    QtWidgets.QApplication.processEvents()
    assert [f.geometry() for f in win._chart_frames] == geos
    win.close()


def test_resize_drag_freezes_other_charts_not_the_dragged_one(app, _synthetic_load):
    """Perf + UX: an edge-resize drag FREEZES the OTHER chart views/bodies for the duration, but
    NEVER the window being dragged.

    The OTHER charts replay a ~10k-candle QPicture (~130ms) and other windows' tables repaint on
    reveal — repainting all of them per resize frame stuck the drag at ~300ms-2s with several
    windows open (measured), so _set_resize_frozen disables them during the drag. But the dragged
    window's OWN chart must keep painting: freezing it gives ~no perf win and leaves its resized
    viewport BLANK until release (the bug this guards)."""
    win = MainWindow(session_path=None)
    win.show()
    QtWidgets.QApplication.processEvents()
    win._new_chart_document("ETHUSDT", "1h")
    win._new_chart_document("SOLUSDT", "1h")
    win.open_tool("journal")
    QtWidgets.QApplication.processEvents()
    frame = win._chart_frames[-1]
    all_views = win.findChildren(QtWidgets.QGraphicsView)
    own = set(frame.findChildren(QtWidgets.QGraphicsView))      # the dragged window's own chart
    others = [v for v in all_views if v not in own]
    assert own, "dragged frame has no chart view"
    assert others, "no other chart views to freeze"
    assert all(v.updatesEnabled() for v in all_views)          # live before a drag

    frame._set_resize_frozen(True)                             # simulate drag start
    assert all(v.updatesEnabled() for v in own), "the dragged window's own chart must NOT freeze"
    assert all(not v.updatesEnabled() for v in others), "other chart views not frozen during resize"

    frame._set_resize_frozen(False)                            # simulate drag release
    assert all(v.updatesEnabled() for v in all_views), "chart views not restored after resize"
    win.close()


def test_link_group_syncs_symbol_and_interval(app, _synthetic_load):
    win = MainWindow(session_path=None)
    d1 = win._new_chart_document("ETHUSDT", "1h")
    d2 = win._new_chart_document("SOLUSDT", "1h")
    d3 = win._new_chart_document("ADAUSDT", "1h")
    d1._set_link_group(3); d2._set_link_group(3); d3._set_link_group(1)   # blue, blue, red

    # watchlist (blue) picks a symbol -> blue charts follow, red one does not
    win._set_watchlist_link(3)
    win.watchlist.symbolChosen.emit("BTCUSDT")
    assert (d1.symbol, d2.symbol, d3.symbol) == ("BTCUSDT", "BTCUSDT", "ADAUSDT")

    # changing one blue chart's interval syncs the other blue chart (symbol + interval), not red
    d1.load(interval="4h")
    assert (d2.symbol, d2.interval) == ("BTCUSDT", "4h")
    assert (d3.symbol, d3.interval) == ("ADAUSDT", "1h")
    win.close()


def test_link_group_set_via_dot_signal_syncs_attr_and_dot(app, _synthetic_load):
    """Picking a colour on the dot updates both the doc's link_group and the dot visual."""
    win = MainWindow(session_path=None)
    d = win._new_chart_document("ETHUSDT", "1h")
    d._link_dot.set_group(4, emit=True)          # simulates the menu pick
    assert d.link_group == 4 and d._link_dot.group() == 4
    win.close()


def test_interval_link_defaults_to_follow_symbol(app, _synthetic_load):
    """A fresh doc's interval channel follows the symbol link (back-compat: one colour = both)."""
    win = MainWindow(session_path=None)
    d = win._new_chart_document("ETHUSDT", "1h")
    assert d.interval_link_group is None          # None == follow symbol
    assert d._ivl_dot.group() == -1               # dot shows the "follow symbol" sentinel
    win.close()


def test_interval_channel_links_timeframe_without_symbol(app, _synthetic_load):
    """MultiCharts parity: charts can share an INTERVAL colour without sharing a symbol colour —
    changing one's timeframe syncs the other's timeframe but NOT its symbol."""
    win = MainWindow(session_path=None)
    a = win._new_chart_document("ETHUSDT", "1h")
    b = win._new_chart_document("SOLUSDT", "1h")
    a._set_interval_link_group(2); b._set_interval_link_group(2)   # interval=green, symbols unlinked

    a.load(interval="4h")
    assert b.interval == "4h"                      # timeframe followed
    assert b.symbol == "SOLUSDT"                   # symbol did NOT follow (channels independent)
    win.close()


def test_interval_unlinked_frees_timeframe_under_symbol_link(app, _synthetic_load):
    """Symbol-linked charts can hold INDEPENDENT timeframes when interval is set to unlinked (0)."""
    win = MainWindow(session_path=None)
    a = win._new_chart_document("ETHUSDT", "1h")
    b = win._new_chart_document("SOLUSDT", "1h")
    a._set_link_group(3); b._set_link_group(3)                    # both symbol-linked (blue)
    a._set_interval_link_group(0); b._set_interval_link_group(0)  # interval explicitly unlinked

    a.load("ETHUSDT", "4h")
    assert b.symbol == "ETHUSDT"                   # symbol still synced
    assert b.interval == "1h"                      # but timeframe stays independent
    win.close()


def test_interval_link_dot_follow_color_round_trip(app, _synthetic_load):
    win = MainWindow(session_path=None)
    d = win._new_chart_document("ETHUSDT", "1h")
    d._ivl_dot.set_group(4, emit=True)            # pick a colour -> decoupled
    assert d.interval_link_group == 4
    d._ivl_dot.set_group(-1, emit=True)           # back to "follow symbol"
    assert d.interval_link_group is None
    win.close()


def test_link_dot_menu_is_multicharts_style(app, _synthetic_load):
    """The colour menu mirrors MultiCharts: swatch icons, a check on the active entry,
    'Linked to all' on top and 'Not linked' at the bottom."""
    from vike_trader_app.ui.linkbus import LINK_ALL
    from vike_trader_app.ui.panels import LinkDot

    dot = LinkDot(0)
    actions = [a for a in dot.menu().actions() if not a.isSeparator()]
    labels = [a.text() for a in actions]
    assert labels[0] == "Linked to all"
    assert labels[-1] == "Not linked"
    assert len(labels) == 17                            # all + 15 colours + not-linked
    assert "Pink" in labels and "Sky blue" in labels
    # colour entries carry swatch icons; the active entry is checked
    assert not next(a for a in actions if a.text() == "Red").icon().isNull()
    assert next(a for a in actions if a.text() == "Not linked").isChecked()
    dot.set_group(LINK_ALL)
    assert next(a for a in actions if a.text() == "Linked to all").isChecked()
    assert not next(a for a in actions if a.text() == "Not linked").isChecked()

    ivl = LinkDot(-1, follow=True)                      # the interval dot adds Follow symbol
    ivl_labels = [a.text() for a in ivl.menu().actions() if not a.isSeparator()]
    assert ivl_labels[0] == "Follow symbol" and len(ivl_labels) == 18


def test_linked_to_all_chart_follows_any_colour(app, _synthetic_load):
    from vike_trader_app.ui.linkbus import LINK_ALL

    win = MainWindow(session_path=None)
    leader = win._new_chart_document("ETHUSDT", "1h")
    follower = win._new_chart_document("SOLUSDT", "1h")
    leader._set_link_group(13)                          # pink (new palette id)
    follower._set_link_group(LINK_ALL)
    leader.load("BTCUSDT", "4h")
    assert (follower.symbol, follower.interval) == ("BTCUSDT", "4h")
    win.close()


def test_interval_link_group_persists_and_restores(app, _synthetic_load, tmp_path):
    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    d = first._new_chart_document("ETHUSDT", "4h")
    d._set_interval_link_group(2)                  # green interval channel
    first.close()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["documents"][0]["interval_link_group"] == 2

    second = MainWindow(session_path=str(path))
    assert second._doc_widgets[0].interval_link_group == 2
    second.close()


def test_focusing_restored_linked_doc_does_not_broadcast(app, monkeypatch):
    """ensure_loaded (focus top-up of a restored doc) must NOT overwrite same-group peers."""
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(_bars()))
    win = MainWindow(session_path=None)
    # peer in blue with its own symbol
    peer = win._new_chart_document("SOLUSDT", "1h")
    peer._set_link_group(3)
    # a "restored" doc: cache-only load (so _loaded stays False -> ensure_loaded will top up)
    restored = win._new_chart_document("ETHUSDT", "1h", network=False, make_current=False)
    restored._set_link_group(3)
    # focusing it triggers ensure_loaded -> must not broadcast ETHUSDT onto the peer
    restored.ensure_loaded()
    assert peer.symbol == "SOLUSDT"
    win.close()


def test_failed_link_load_rolls_back_symbol(app, monkeypatch):
    """A failed link load to a bad symbol must roll the doc back to its real symbol (async path):
    load() clears the view + kicks an off-thread fetch; the fetch fails -> topup_failed restores
    the previous symbol's cached view, so the doc never gets stuck mislabeled."""
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(_bars()))
    monkeypatch.setattr("vike_trader_app.data.cache.append_series", lambda *a, **k: None)
    doc = ChartDocument("ETHUSDT", "1h"); hub = LiveHub(); hub.register(doc)
    monkeypatch.setattr(hub, "request_topup", lambda d, gen: None)  # worker stubbed (DISABLE_LIVE)
    doc.load(); doc.apply_topup(doc._load_gen, _bars())            # ETHUSDT loaded with bars
    assert doc.symbol == "ETHUSDT"
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult([]))  # BADSYM uncached
    doc.apply_link("BADSYM", "1h")                                 # switch -> view cleared, fetch pending
    assert doc.symbol == "BADSYM" and doc._bars == []
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(_bars()))  # prev cache
    doc.topup_failed(doc._load_gen, "bad symbol")                  # fetch fails -> roll back
    assert doc.symbol == "ETHUSDT" and doc.title() == "ETHUSDT · 1h"
    hub.shutdown()


def test_out_of_range_link_group_clamps_to_unlinked(app, _synthetic_load):
    from vike_trader_app.ui.linkbus import LINK_ALL

    doc = ChartDocument("ETHUSDT", "1h")
    doc.load()
    doc.apply_state({"link_group": 1234, "indicators": []})   # unknown id -> unlinked
    assert doc.link_group == 0
    doc.apply_state({"link_group": LINK_ALL, "indicators": []})  # 99 is now a REAL group
    assert doc.link_group == LINK_ALL


def test_unlinked_documents_do_not_follow(app, _synthetic_load):
    win = MainWindow(session_path=None)
    d1 = win._new_chart_document("ETHUSDT", "1h")     # group 0 (unlinked) by default
    win._set_watchlist_link(0)
    win.watchlist.symbolChosen.emit("BTCUSDT")
    assert d1.symbol == "ETHUSDT"                      # unlinked -> unaffected
    win.close()


def test_link_group_persists_and_restores(app, _synthetic_load, tmp_path):
    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    d = first._new_chart_document("ETHUSDT", "4h")
    d._set_link_group(2)                               # green
    first._set_watchlist_link(2)
    first.close()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["documents"][0]["link_group"] == 2
    assert saved["watchlist_link"] == 2

    second = MainWindow(session_path=str(path))
    assert second._doc_widgets[0].link_group == 2
    assert second._watchlist_link == 2
    second.close()


def test_documents_persist_and_restore(app, _synthetic_load, tmp_path):
    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    first._new_chart_document("ETHUSDT", "4h")
    d = first._new_chart_document("SOLUSDT", "1h")
    d.chart.add_indicator("rsi", params={"period": 14})
    first.close()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [doc["symbol"] for doc in saved["documents"]] == ["ETHUSDT", "SOLUSDT"]

    second = MainWindow(session_path=str(path))
    assert len(second._chart_frames) == 2
    syms = [doc.symbol for doc in second._doc_widgets]
    assert syms == ["ETHUSDT", "SOLUSDT"]
    # the SOL doc's indicator was restored (cache-only load produced bars, so it re-attached)
    sol = second._doc_widgets[1]
    assert [i.name for i in sol.chart._indicators.values()] == ["rsi"]
    second.close()


def test_symbol_box_drives_focused_chart(app, _synthetic_load):
    """Chart-unify keystone: the symbol box / watchlist drive the FOCUSED chart WINDOW. There is no
    central chart to fall back to, so with nothing focused the symbol box is a no-op."""
    win = MainWindow(session_path=None)
    win.show(); app.processEvents()

    # (1) nothing focused + no central chart -> symbol box does nothing (no resurrected chart)
    win._set_active_frame(None)
    win._load_symbol("ADAUSDT"); app.processEvents()
    assert win._active_chart_doc() is None
    assert win.price is None                   # no focused chart -> price tracks nothing

    # (2) focus a chart window -> the symbol box drives THAT doc
    doc = win._new_chart_document("SOLUSDT", "1h", make_current=True); app.processEvents()
    _focus_and_load(win, win._chart_frames[-1], "ETHUSDT"); app.processEvents()
    assert doc.symbol == "ETHUSDT"
    assert win.price is doc.chart              # the focused doc IS the active chart

    # (3) open a SECOND chart and focus it -> the symbol box drives the new doc; the first is left alone
    doc2 = win._new_chart_document("XRPUSDT", "1h", make_current=True); app.processEvents()
    _focus_and_load(win, win._chart_frames[-1], "BNBUSDT"); app.processEvents()
    assert doc2.symbol == "BNBUSDT"
    assert doc.symbol == "ETHUSDT"             # the unfocused doc is untouched
    win.close()


def test_symbol_box_no_op_when_no_chart_open(app, _synthetic_load):
    """Chart-unify keystone: with NO chart window open/focused the symbol box does NOTHING — there is
    no central chart to auto-open/resurrect. (This is what keeps an empty saved workspace empty: the
    startup auto-load no-ops here instead of forcing a chart back open.) Focusing a chart re-arms it."""
    win = MainWindow(session_path=None)
    win.show(); app.processEvents()

    # no chart open at all (fresh bare window) -> the symbol box no-ops, nothing is created
    win._load_symbol("ADAUSDT"); app.processEvents()
    assert win._chart_frames == []             # no chart auto-opened
    assert win.price is None

    # open + focus a chart -> the symbol box now drives it
    doc = win._new_chart_document("SOLUSDT", "1h", make_current=True); app.processEvents()
    _focus_and_load(win, win._chart_frames[-1], "XRPUSDT"); app.processEvents()
    assert doc.symbol == "XRPUSDT"             # focused chart -> loads

    # close the chart frame -> back to no-op (no resurrection on the next symbol)
    win._chart_frames[-1].close_window(); app.processEvents()
    assert win._chart_frames == []
    assert win._active_chart_doc() is None      # nothing focused after the close
    win._load_symbol("ETHUSDT"); app.processEvents()
    assert win._chart_frames == []             # CLOSED -> no-op, no auto-open
    assert win._active_chart_doc() is None
    win.close()
