# src/vike_trader_app/ui/live_feed_worker.py
"""Run the vike.io WS LiveBarFeed off the Qt main thread; marshal each CLOSED bar to the
main thread via a queued barClosed signal.

Mirrors the PrivateUserDataWorker pattern:
- run() wraps the async feed coroutine in asyncio.run() so the QThread worker body is sync.
- A threading.Event (_stop) gates the stop-predicate passed into run_forever.
- stop() sets the event then wait()s the thread (the 0xC0000409 teardown invariant).

Task 3 (LiveStrategyPump wiring) registers the worker on LiveExecutionSession so shutdown()
joins it automatically.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from PySide6 import QtCore

log = logging.getLogger(__name__)


class LiveBarFeedWorker(QtCore.QThread):
    """QThread that drives a LiveBarFeed and emits each CLOSED bar via a queued signal.

    Usage::

        feed = make_live_feed(symbol, interval)
        worker = LiveBarFeedWorker(feed)
        worker.barClosed.connect(pump.feed_bar)   # queued -> main thread
        worker.start()
        ...
        worker.stop()   # sets stop flag + wait()s the thread
    """

    barClosed = QtCore.Signal(object)   # Bar; queued connection -> delivered on the main thread

    def __init__(self, feed, parent=None) -> None:
        super().__init__(parent)
        self._feed = feed
        self._stop = threading.Event()

    # ------------------------------------------------------------------
    # QThread.run() — executes on the worker thread
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Drive run_forever in a private asyncio event loop until stop() is called."""
        try:
            asyncio.run(
                self._feed.run_forever(
                    self._emit,
                    stop=lambda: self._stop.is_set(),
                )
            )
        except Exception:   # noqa: BLE001
            log.exception("LiveBarFeedWorker crashed")

    def _emit(self, bar) -> None:
        """Emit barClosed; Qt uses a queued connection -> main thread delivery."""
        self.barClosed.emit(bar)

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    def stop(self, timeout_ms: int = 5000) -> None:
        """Signal the feed to stop and wait for the thread to exit.

        Safe to call multiple times or before start().
        """
        self._stop.set()
        if self.isRunning():
            self.wait(timeout_ms)
