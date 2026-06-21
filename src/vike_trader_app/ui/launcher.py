"""Thin desktop entry point — defers the heavy GUI import into ``main()``.

The ``vike-trader-app-gui`` console script targets THIS module instead of :mod:`.app`. Why: when
the optimizer fans a grid sweep out to ``ProcessPoolExecutor`` workers, Windows ``spawn`` re-imports
the launch process's ``__main__`` (the console-script wrapper) in every worker. If that wrapper pulls
in :mod:`.app`, each worker re-imports the whole PySide6 / pyqtgraph / UI stack — measured ~5.8 s cold
— even though the worker only needs the tester engine (~0.1 s). Pointing the wrapper at this module,
whose import is stdlib-light, drops that to a no-op: the real app is imported lazily, only when
``main()`` actually runs in the GUI process.

This changes nothing about how the app runs — ``main()`` simply forwards to :func:`.app.main`.
"""

from __future__ import annotations


def main() -> int | None:
    """Launch the desktop app. The heavy GUI import happens HERE, not at module import time."""
    from .app import main as _app_main

    return _app_main()


if __name__ == "__main__":
    raise SystemExit(main())
