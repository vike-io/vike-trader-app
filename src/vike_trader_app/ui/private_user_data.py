# src/vike_trader_app/ui/private_user_data.py
"""Qt shell for the live user-data stream: a QThread worker + a main-thread marshalling QObject.

PrivateUserDataWorker clones _LiveFeedWorker (app.py:99-119): run() drives the async core off-thread
and emits frozen events via report=Signal(object); failed=Signal(str) SCRUBS secrets first. The
worker NEVER touches the bus/account. LiveExecutionSession.(_on_report) is the ONLY code that calls
bus.publish — the single-main-thread-writer rule. Qt auto-uses a QUEUED connection because the slot
lives on a main-thread QObject. shutdown() stop()+wait(2000)s every worker (the 0xC0000409 invariant)
then calls hub.shutdown().
"""

from __future__ import annotations

import os
import re

from PySide6 import QtCore

_SECRET_RE = re.compile(r"(signature|secret|api[_-]?key|x-mbx-apikey)=\S+", re.IGNORECASE)


def _scrub(message: str) -> str:
    return _SECRET_RE.sub(r"\1=***", message or "")


class PrivateUserDataWorker(QtCore.QThread):
    """Runs the user-data async core off the UI thread; marshals events back via a signal."""

    report = QtCore.Signal(object)   # frozen FillEvent / Order* event
    failed = QtCore.Signal(str)

    def __init__(self, run_core) -> None:
        super().__init__()
        self._run_core = run_core   # callable(emit, stop) -> None (sync; wraps asyncio.run inside)
        self._stop = False

    def run(self) -> None:
        try:
            self._run_core(self.report.emit, lambda: self._stop)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI thread, secret-scrubbed
            self.failed.emit(_scrub(str(exc)))

    def stop(self) -> None:
        self._stop = True


class LiveExecutionSession(QtCore.QObject):
    """Owns the per-venue worker dict; its main-thread slot is the only bus.publish caller."""

    def __init__(self, hub) -> None:
        super().__init__()
        self._hub = hub
        self._workers: dict[str, PrivateUserDataWorker] = {}
        self._closing = False

    def add_worker(self, key: str, worker: PrivateUserDataWorker) -> None:
        worker.report.connect(self._on_report)        # queued: slot is on this main-thread QObject
        worker.failed.connect(self._on_failed)
        self._workers[key] = worker

    def add_worker_if_enabled(self, key: str, worker: PrivateUserDataWorker) -> bool:
        """Register + start a worker unless VIKE_DISABLE_LIVE is set (the headless kill-switch)."""
        if os.environ.get("VIKE_DISABLE_LIVE"):
            return False
        self.add_worker(key, worker)
        worker.start()
        return True

    def _on_report(self, event) -> None:
        if self._closing or self._hub is None:
            return  # a late queued event during teardown no-ops (mirror app.py:2960)
        self._hub.bus.publish(event)

    def _on_failed(self, message: str) -> None:
        # No modal in a non-interactive path; the message is already secret-scrubbed.
        import logging

        logging.getLogger("vike.exec").warning("live user-data worker failed: %s", message)

    def shutdown(self) -> None:
        """stop()+wait() every worker (the 0xC0000409 invariant), then detach the hub."""
        self._closing = True
        for worker in self._workers.values():
            worker.stop()
            worker.wait(2000)
        self._workers.clear()
        if self._hub is not None:
            self._hub.shutdown()
            self._hub = None
