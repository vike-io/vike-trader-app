"""Application-wide logging configuration.

A single :func:`configure_logging` call wires the **root** logger to two sinks:

* a **size-rotating file** at ``logs/vike-trader.log`` (5 backups x ~2 MB) so a
  long-running desktop session never grows an unbounded log — the file captures
  everything at ``DEBUG``. ``logs/`` is the gitignored home for session artifacts
  (``.gitignore``); ``storage/`` is for *data* and only ignores ``*.json``/parquet/db
  by extension, so a ``.log`` there would leak into git; and
* a **console** handler at a higher level (``INFO`` by default) so the terminal stays
  readable — colorized via :mod:`rich` when it is importable, a plain
  :class:`logging.StreamHandler` otherwise.

Module code should *never* configure handlers itself — it just does
``log = logging.getLogger(__name__)`` and logs. Only the app entry points
(``ui/app.py``, the CLIs) call :func:`configure_logging`, once, at startup.

Env overrides (consistent with ``VIKE_DATA_ROOT`` / ``VIKE_TELEMETRY_DIR``):
  * ``VIKE_LOG_DIR``   — directory for the log file (default ``logs``).
  * ``VIKE_LOG_LEVEL`` — console level, e.g. ``DEBUG``/``WARNING`` (default ``INFO``).
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

#: Noisy third-party loggers pinned to WARNING so they never flood the file log.
_NOISY = ("urllib3", "matplotlib", "PIL", "asyncio", "websockets", "httpx", "httpcore")

_configured = False  # guard so a second call is a no-op (idempotent)


def _log_dir() -> Path:
    return Path(os.environ.get("VIKE_LOG_DIR") or "logs")


def _console_level() -> int:
    name = (os.environ.get("VIKE_LOG_LEVEL") or "INFO").upper()
    return getattr(logging, name, logging.INFO)


def configure_logging(*, force: bool = False) -> Path | None:
    """Attach rotating-file + console handlers to the root logger.

    Idempotent: repeat calls are ignored unless ``force=True``. Returns the path to
    the active log file, or ``None`` if the file sink could not be opened (a locked /
    read-only storage dir must never block app launch — console logging still works).
    """
    global _configured
    if _configured and not force:
        return getattr(configure_logging, "_path", None)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # the file handler wants everything; handlers filter down

    # Drop any handlers we previously attached (force-reconfigure / tests).
    for h in list(root.handlers):
        if getattr(h, "_vike_managed", False):
            root.removeHandler(h)

    file_fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_path: Path | None = None
    try:
        d = _log_dir()
        d.mkdir(parents=True, exist_ok=True)
        log_path = d / "vike-trader.log"
        fh = RotatingFileHandler(
            log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8", delay=True
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(file_fmt)
        fh._vike_managed = True  # type: ignore[attr-defined]
        root.addHandler(fh)
    except OSError:  # locked / read-only storage — fall back to console only
        log_path = None

    # Console: rich if available (nice colors + tracebacks), else a plain stream.
    ch: logging.Handler
    try:
        from rich.logging import RichHandler

        ch = RichHandler(rich_tracebacks=True, show_path=False, log_time_format="%H:%M:%S")
        ch.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    except Exception:  # noqa: BLE001 - rich is optional; stderr stream always works
        ch = logging.StreamHandler(stream=sys.stderr)
        ch.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
        )
    ch.setLevel(_console_level())
    ch._vike_managed = True  # type: ignore[attr-defined]
    root.addHandler(ch)

    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.captureWarnings(True)  # route warnings.warn(...) into the "py.warnings" logger

    _configured = True
    configure_logging._path = log_path  # type: ignore[attr-defined]
    return log_path
