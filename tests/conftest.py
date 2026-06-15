"""Shared pytest configuration.

Auto-applies the ``gui`` and ``network`` markers so the fast, deterministic core can be run with
``pytest -m "not gui and not network"`` — GUI tests need a Qt QApplication (slow) and a few hit the
live network off-thread, which can segfault the whole run (see CLAUDE.md, data-layer thread-unsafety).

No per-file edits needed: a test is treated as GUI if its file is ``*_gui.py`` OR it uses one of the
Qt fixtures below (catches ``test_chart_*``/``test_forward_ui`` etc. that drive a real window).
"""
import os
import sys

import pytest

# Session persistence is disabled for the whole suite: tests construct MainWindow() bare, and a
# developer's local storage/session.json would otherwise leak state (start space, symbol, panel
# toggles) into them. Session tests opt back in by passing an explicit session_path (the env
# kill-switch only applies to the default path — see MainWindow.__init__).
os.environ.setdefault("VIKE_DISABLE_SESSION", "1")

# Live chart auto-updates are disabled suite-wide: _arm_live_updates spawns _LiveFetchWorker
# threads that hit the real network (Binance/Yahoo). A MainWindow left un-closed by one test
# keeps polling, and its fetched->render callbacks fire during a later, unrelated test's
# processEvents() — real network in a headless run, which stalls/segfaults the suite (see
# CLAUDE.md: data layer not thread-safe; no network in non-interactive paths). The live-edge
# merge logic is unit-tested directly in tests/unit/data/test_live_update.py.
os.environ.setdefault("VIKE_DISABLE_LIVE", "1")

_GUI_FIXTURES = {"app", "qapp", "qtbot", "win", "main_window", "mainwindow"}

# Tests known to hit the LIVE network (real Binance/Yahoo fetch). These can segfault when the fetch
# runs on a background thread during GC, so they are also excludable via ``-m "not network"``.
_NETWORK_TESTS = {
    # The datamanager update/download paths spawn a background loader that hits Binance directly
    # (bypassing the foreground get_bars monkeypatch), which can segfault during GC — exclude them.
    "test_datamanager_update_all_extends_each_series",
    "test_datamanager_download_dataset_iterates_symbols",
    "test_datamanager_download_series_routes_through_chain",
}


@pytest.fixture(autouse=True)
def _no_background_quotes(monkeypatch):
    """Disable the watchlist quote timers in tests (mirrors VIKE_DISABLE_LIVE for the chart feed).

    ``_populate_watchlist`` (in MainWindow.__init__) starts a 10ms ``_price_timer`` that does a
    Catalog/Parquet read + ``watchlist.set_prices`` every tick, then a 6s ``_refresh_timer``. Under
    parallel xdist load a heavy GUI test runs long enough that those ticks fire *during* dock/window
    teardown — the reentrant data-layer read and ``set_prices`` on a torn-down label intermittently
    crash the worker (``CDockWidget already deleted`` / native ``worker crashed``). In isolation the
    test finishes before a tick matters, so it passes — the classic xdist-only flake. No test depends
    on live quotes, so no-op the two starters. Reads ``sys.modules`` (never imports Qt) so the
    Qt-free unit run stays untouched."""
    app_mod = sys.modules.get("vike_trader_app.ui.app")
    if app_mod is None:
        return  # Qt-free unit run — app never imported, nothing (and no Qt) to touch
    monkeypatch.setattr(app_mod.MainWindow, "_start_price_fill", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(app_mod.MainWindow, "_start_quote_refresh", lambda *a, **k: None,
                        raising=False)


@pytest.fixture(autouse=True)
def _cleanup_qt_widgets():
    """Tear down leaked Qt widget trees deterministically at each test boundary.

    GUI tests create ``MainWindow()``s and ``.close()`` them but never *delete* them, so the C++
    objects — and their many pyqtgraph chart items — linger. Left to Python's garbage collector,
    those wrappers are finalized in arbitrary order (at inter-test or interpreter-shutdown
    collection), and pyqtgraph/PySide6 teardown then frees a parent before its child → an
    intermittent ``0xC0000005`` access violation (~20% of offscreen ``tests/gui`` runs on
    Windows/py3.14; never seen on CI Linux/py3.12). ``deleteLater()`` + ``processEvents()`` here
    makes Qt delete each tree (parent then children, correct order) while it's still intact, at a
    single-threaded idle moment; Python's GC then only collects empty wrappers.

    Why deleteLater and NOT ``close()``: pyqtgraph's ``PlotWidget.close()`` is not safe to call
    externally (it nulls ``plotItem`` then a second close AttributeErrors), and the tests already
    close their own windows — we only need to *delete* the leftovers. Every call is guarded: a
    widget can be torn down by another's deletion (or by the test's own fixture) between the
    snapshot and the call, raising ``RuntimeError: C++ object already deleted``.

    Reads ``sys.modules`` instead of importing PySide6, so it is a true no-op for the Qt-free
    unit run (and the CI unit job, where PySide6 isn't even installed) — never pulling Qt into
    that interpreter.
    """
    yield
    qtw = sys.modules.get("PySide6.QtWidgets")
    if qtw is None:
        return
    app = qtw.QApplication.instance()
    if app is None:
        return
    for w in list(app.topLevelWidgets()):
        try:
            w.deleteLater()
        except RuntimeError:
            pass  # C++ object already gone (deleted with another tree / by the test's fixture)
    app.processEvents()


def pytest_collection_modifyitems(config, items):
    for item in items:
        fname = item.fspath.basename
        fixtures = set(getattr(item, "fixturenames", ()))
        if fname.endswith("_gui.py") or "_gui" in fname or (fixtures & _GUI_FIXTURES):
            item.add_marker(pytest.mark.gui)
        if item.originalname in _NETWORK_TESTS or item.name in _NETWORK_TESTS:
            item.add_marker(pytest.mark.network)
