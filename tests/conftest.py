"""Shared pytest configuration.

Auto-applies the ``gui`` and ``network`` markers so the fast, deterministic core can be run with
``pytest -m "not gui and not network"`` — GUI tests need a Qt QApplication (slow) and a few hit the
live network off-thread, which can segfault the whole run (see CLAUDE.md, data-layer thread-unsafety).

No per-file edits needed: a test is treated as GUI if its file is ``*_gui.py`` OR it uses one of the
Qt fixtures below (catches ``test_chart_*``/``test_forward_ui`` etc. that drive a real window).
"""
import os

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


def pytest_collection_modifyitems(config, items):
    for item in items:
        fname = item.fspath.basename
        fixtures = set(getattr(item, "fixturenames", ()))
        if fname.endswith("_gui.py") or "_gui" in fname or (fixtures & _GUI_FIXTURES):
            item.add_marker(pytest.mark.gui)
        if item.originalname in _NETWORK_TESTS or item.name in _NETWORK_TESTS:
            item.add_marker(pytest.mark.network)
