# src/vike_trader_app/ui/private_user_data.py
"""Qt shell for the live user-data stream: a QThread worker + a main-thread marshalling QObject.

PrivateUserDataWorker clones _LiveFeedWorker (app.py:99-119): run() drives the async core off-thread
and emits frozen events via report=Signal(object); failed=Signal(str) passes the message through a
best-effort secret scrub (_scrub). The PRIMARY secrets-never-logged guarantee is STRUCTURAL — the
worker closure is the only holder of the creds, events carry no creds, and UserDataAuthError carries
only ret_msg — so the scrub is a defense-in-depth second line, not the guarantee. The worker NEVER
touches the bus/account. LiveExecutionSession.(_on_report) is the ONLY code that calls bus.publish —
the single-main-thread-writer rule. Qt auto-uses a QUEUED connection because the slot lives on a
main-thread QObject. shutdown() stop()+wait(2000)s every worker (the 0xC0000409 invariant) then calls
hub.shutdown().
"""

from __future__ import annotations

import logging
import os
import re

from PySide6 import QtCore

# Defense-in-depth scrub for the failed=Signal(str) path. The PRIMARY guarantee is STRUCTURAL: the
# worker closure is the only holder of the secret, events carry no creds, and UserDataAuthError carries
# only ret_msg. This scrub is a best-effort second line for any future error string that embeds a
# key/secret/sign token. The (?<![A-Za-z]) left-boundary keeps benign words like `design` from matching
# the `sign` branch.
_SECRET_RE = re.compile(
    r"(?<![A-Za-z])(signature|sign|secret|api[_-]?key|x-mbx-apikey)\s*[=:]\s*\S+", re.IGNORECASE)
_AUTH_ARGS_RE = re.compile(r'("op"\s*:\s*"auth"\s*,\s*"args"\s*:\s*\[)[^\]]*(\])', re.IGNORECASE)


def _scrub(message: str) -> str:
    s = _AUTH_ARGS_RE.sub(r"\1***\2", message or "")   # redact api_key + sign in a stringified auth frame
    return _SECRET_RE.sub(r"\1=***", s)


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

    @property
    def hub(self):
        """The live LiveOmsHub while armed; None after shutdown(). The order ticket reaches the armed
        hub (.symbol/.venue/.bus/.account/.submit_ticket) through this single accessor."""
        return self._hub

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
            if not worker.wait(2000):
                # The worker did not join in 2s — e.g. blocked in a SYNC listenKey REST create during a
                # reconnect (a blocking urllib call cannot be interrupted mid-flight). It must NOT be left
                # running into app teardown / os._exit (the 0xC0000409 class). Extend the join window to
                # cover the worst-case bounded sync-HTTP timeout rather than abandoning a live thread.
                logging.getLogger("vike.exec").warning(
                    "user-data worker did not join in 2s; extending the join window")
                worker.wait(8000)
        self._workers.clear()
        if self._hub is not None:
            self._hub.shutdown()
            self._hub = None
