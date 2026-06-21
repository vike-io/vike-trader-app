"""The thin GUI launcher must (1) defer the heavy app import — so optimizer ProcessPool workers that
re-import __main__ stay light — yet (2) still forward to app.main() so the app launches unchanged."""

import subprocess
import sys
from unittest import mock


def test_launcher_import_does_not_pull_in_app_or_qt():
    """Importing the launcher in a FRESH interpreter must not drag in ui.app / PySide6 (the whole
    point — that 5.8 s GUI import is what we keep out of every spawned worker)."""
    code = (
        "import sys, vike_trader_app.ui.launcher; "
        "assert 'vike_trader_app.ui.app' not in sys.modules, 'launcher eagerly imported ui.app'; "
        "assert 'PySide6.QtWidgets' not in sys.modules, 'launcher eagerly imported PySide6'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_launcher_main_forwards_to_app_main():
    from vike_trader_app.ui import launcher

    with mock.patch("vike_trader_app.ui.app.main", return_value=7) as app_main:
        rv = launcher.main()
    app_main.assert_called_once_with()
    assert rv == 7
