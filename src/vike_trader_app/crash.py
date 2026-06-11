"""Tier-0 crash capture + best-effort reporting.

Captures every crash locally — uncaught Python exceptions (main thread and worker threads),
Qt fatal messages, and native segfaults (via :mod:`faulthandler`) — and writes a PII-scrubbed
event to a spool under ``logs/pending_crash/``.

A crashing process is the worst place to do network I/O (a dying interpreter or a hard segfault
usually can't finish a POST), so nothing is uploaded mid-crash. The next *healthy* launch calls
:func:`drain_pending`, which hands each spooled crash to :func:`vike_trader_app.ai.telemetry.report_crash`
(opt-in) and deletes it. Native faults land in ``logs/faulthandler.log`` and are ingested on the
next launch.

:func:`install` is called once from ``ui/app.py`` ``main()``. Module code never touches this.
"""

from __future__ import annotations

import faulthandler
import json
import logging
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from .ai import telemetry
from .logging_setup import _log_dir

log = logging.getLogger("vike.crash")

_MAX_SPOOL = 20  # newest N spooled crashes uploaded per launch (bounds startup cost)
_installed = False
_app_version: str | None = None
_fault_file = None  # keep the faulthandler sink handle alive for the process lifetime


# --- locations (share logging_setup's log dir; honor VIKE_LOG_DIR) --------------------

def _spool_dir() -> Path:
    return _log_dir() / "pending_crash"


def _fault_log() -> Path:
    return _log_dir() / "faulthandler.log"


# --- scrubbing (no usernames / absolute paths leave the machine) ----------------------

def _scrub_path(s: str) -> str:
    """Rewrite an absolute path to package/home-relative so the OS username never leaks."""
    norm = s.replace("\\", "/")
    for anchor in ("/site-packages/", "/dist-packages/", "/src/"):
        i = norm.find(anchor)
        if i != -1:
            return norm[i + len(anchor):]
    i = norm.find("vike_trader_app/")
    if i != -1:
        return norm[i:]
    home = str(Path.home()).replace("\\", "/")
    if home and norm.startswith(home):
        return "~" + norm[len(home):]
    return norm


def _scrub_traceback(tb_text: str) -> str:
    """Scrub the path inside each ``File "..."`` line; leave message bodies untouched."""
    out = []
    for line in tb_text.splitlines():
        if line.lstrip().startswith('File "'):
            try:
                pre, rest = line.split('"', 1)
                path, post = rest.split('"', 1)
                line = f'{pre}"{_scrub_path(path)}"{post}'
            except ValueError:
                pass
        out.append(line)
    return "\n".join(out)


# --- event build + spool --------------------------------------------------------------

def _build_event(kind, exc_type=None, exc=None, tb=None, tb_text=None) -> dict:
    if tb_text is None:
        tb_text = "".join(traceback.format_exception(exc_type, exc, tb))
    return {
        "kind": kind,
        "exc_type": getattr(exc_type, "__name__", None) if exc_type else None,
        "traceback": _scrub_traceback(tb_text),
        "app_version": _app_version,
        "ts_ms": int(time.time() * 1000),
    }


def _spool(event: dict) -> None:
    try:
        d = _spool_dir()
        d.mkdir(parents=True, exist_ok=True)
        name = f"{event.get('ts_ms', 0)}-{uuid.uuid4().hex[:8]}.json"
        (d / name).write_text(json.dumps(event), encoding="utf-8")
    except Exception:  # noqa: BLE001 - a locked/unwritable spool must never break a crash path
        pass


def _safe_unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


# --- handlers (must NEVER raise) ------------------------------------------------------

def _handle(kind, exc_type, exc, tb) -> None:
    """Log CRITICAL + spool one scrubbed crash event. Swallows all of its own errors."""
    try:
        event = _build_event(kind, exc_type, exc, tb)
        log.critical("uncaught %s [%s]:\n%s", kind, event.get("exc_type"), event["traceback"])
        _spool(event)
    except Exception:  # noqa: BLE001
        pass


def report_qt(mode: int, msg: str) -> None:
    """Spool a Qt fatal/critical message as a crash (called by the Qt message handler)."""
    try:
        log.critical("qt fatal: %s", msg)
        _spool(_build_event("qt_fatal", tb_text=str(msg)))
    except Exception:  # noqa: BLE001
        pass


# --- drain (next-launch upload) -------------------------------------------------------

def _ingest_faulthandler_log() -> None:
    """Convert a non-empty native-fault log into a spooled ``native`` event, then truncate it."""
    fp = _fault_log()
    if not fp.exists():
        return
    text = fp.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return
    _spool(_build_event("native", tb_text=text))
    fp.write_text("", encoding="utf-8")  # rotate so the same fault isn't re-reported


def drain_pending() -> int:
    """Upload spooled crashes via telemetry, then delete them. Returns count reported.

    Processes at most ``_MAX_SPOOL`` newest spool files per launch; any excess older files are
    deleted unreported (the local ``logs/`` already holds their detail). Never raises.
    """
    try:
        _ingest_faulthandler_log()
    except Exception:  # noqa: BLE001
        pass

    count = 0
    try:
        d = _spool_dir()
        files = sorted(d.glob("*.json")) if d.exists() else []
        if len(files) > _MAX_SPOOL:  # drop oldest excess unreported
            for old in files[:-_MAX_SPOOL]:
                _safe_unlink(old)
            files = files[-_MAX_SPOOL:]
        for f in files:
            try:
                telemetry.report_crash(json.loads(f.read_text(encoding="utf-8")))
                count += 1
            except Exception:  # noqa: BLE001 - one bad file can't stall the queue
                pass
            finally:
                _safe_unlink(f)
    except Exception:  # noqa: BLE001
        pass
    return count


# --- install --------------------------------------------------------------------------

def install(app_version: str | None = None) -> None:
    """Wire crash hooks (Python main + threads, faulthandler) and drain last run's spool.

    Idempotent. Local capture is unconditional; *upload* of the drained spool is gated by the
    telemetry opt-in inside :func:`telemetry.report_crash`.
    """
    global _installed, _app_version, _fault_file
    if _installed:
        return
    _app_version = app_version

    prev_excepthook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            _handle("python_main", exc_type, exc, tb)
        prev_excepthook(exc_type, exc, tb)

    sys.excepthook = _excepthook

    prev_threadhook = threading.excepthook

    def _threadhook(args):
        if not issubclass(args.exc_type, (KeyboardInterrupt, SystemExit)):
            _handle("python_thread", args.exc_type, args.exc_value, args.exc_traceback)
        prev_threadhook(args)

    threading.excepthook = _threadhook

    # Drain LAST session's spool + native-fault log BEFORE reopening the fault sink for THIS run.
    try:
        drain_pending()
    except Exception:  # noqa: BLE001
        pass

    try:
        fp = _fault_log()
        fp.parent.mkdir(parents=True, exist_ok=True)
        _fault_file = open(fp, "w", encoding="utf-8")  # noqa: SIM115 - kept open process-lifetime
        faulthandler.enable(file=_fault_file, all_threads=True)
    except Exception:  # noqa: BLE001 - faulthandler is a bonus; never block launch
        pass

    _installed = True
