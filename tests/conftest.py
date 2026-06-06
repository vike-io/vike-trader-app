"""Shared pytest configuration.

Auto-applies the ``gui`` and ``network`` markers so the fast, deterministic core can be run with
``pytest -m "not gui and not network"`` — GUI tests need a Qt QApplication (slow) and a few hit the
live network off-thread, which can segfault the whole run (see CLAUDE.md, data-layer thread-unsafety).

No per-file edits needed: a test is treated as GUI if its file is ``*_gui.py`` OR it uses one of the
Qt fixtures below (catches ``test_chart_*``/``test_forward_ui`` etc. that drive a real window).
"""
import pytest

_GUI_FIXTURES = {"app", "qapp", "qtbot", "win", "main_window", "mainwindow"}

# Tests known to hit the LIVE network (real Binance/Yahoo fetch). These can segfault when the fetch
# runs on a background thread during GC, so they are also excludable via ``-m "not network"``.
_NETWORK_TESTS = {
    "test_datamanager_update_all_extends_each_series",
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        fname = item.fspath.basename
        fixtures = set(getattr(item, "fixturenames", ()))
        if fname.endswith("_gui.py") or "_gui" in fname or (fixtures & _GUI_FIXTURES):
            item.add_marker(pytest.mark.gui)
        if item.originalname in _NETWORK_TESTS or item.name in _NETWORK_TESTS:
            item.add_marker(pytest.mark.network)
