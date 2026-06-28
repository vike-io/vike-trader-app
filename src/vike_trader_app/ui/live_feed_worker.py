# src/vike_trader_app/ui/live_feed_worker.py
"""Run the vike.io WS LiveBarFeed off the Qt main thread; marshal each CLOSED bar to the
main thread via a queued barClosed signal.

Mirrors the PrivateUserDataWorker pattern:
- run() drives the async feed coroutine in a private event loop so the QThread body is sync.
- A threading.Event (_stop) gates the stop-predicate passed into run_forever.
- stop() sets the event AND cancels the task cross-thread, then wait()s the thread (the
  0xC0000409 teardown invariant). Cancellation is REQUIRED: the stop predicate alone cannot
  unblock a quiet ``await ws.recv()`` (recv only returns on a frame), so on a silent socket
  the thread would orphan and crash at app-close — cancel propagates through run's
  ``finally: await ws.close()`` and ends the thread promptly.

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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # QThread.run() — executes on the worker thread
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Drive run_forever in a private asyncio event loop until stop() is called.

        The loop + task are held so stop() can CANCEL the task cross-thread — cancellation
        unblocks a quiet ``await ws.recv()`` (which the stop predicate alone cannot, since
        recv only returns on a frame), letting the thread end promptly. LiveBarFeed.run's
        ``finally: await ws.close()`` runs on cancellation; run_forever's ``except Exception``
        does NOT swallow CancelledError (it is BaseException), so the cancel propagates here.
        """
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._task = self._loop.create_task(
                self._feed.run_forever(self._emit, stop=lambda: self._stop.is_set())
            )
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass   # clean cancel-driven teardown (stop() cancelled the task)
        except Exception:   # noqa: BLE001
            log.exception("LiveBarFeedWorker crashed")
        finally:
            if self._loop is not None:
                self._loop.close()

    def _emit(self, bar) -> None:
        """Emit barClosed; Qt uses a queued connection -> main thread delivery."""
        self.barClosed.emit(bar)

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    def stop(self, timeout_ms: int = 5000) -> None:
        """Signal the feed to stop and wait for the thread to exit.

        Sets the stop event AND cancels the running task cross-thread so a feed parked in a
        quiet ``await ws.recv()`` unblocks immediately (the 0xC0000409 orphan-thread fix).
        Safe to call multiple times or before start() (guards cover: never-started, the
        start()->loop-assignment race, and a second call after the loop has closed).
        """
        self._stop.set()
        # Resolve the loop/task, tolerating the brief race where start() has returned (isRunning
        # is True) but run() hasn't reached create_task yet — without this a blocked feed (one that
        # never checks the stop predicate) would be orphaned because stop() saw loop/task as None.
        loop, task = self._loop, self._task
        if (loop is None or task is None) and self.isRunning():
            for _ in range(200):   # up to ~2s
                loop, task = self._loop, self._task
                if loop is not None and task is not None:
                    break
                self.msleep(10)
        if loop is not None and task is not None:
            try:
                # Unblocks ws.recv() -> run's finally closes ws -> thread ends promptly.
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass   # loop already closed (run() finished / second stop() call) — nothing to cancel
        if self.isRunning():
            self.wait(timeout_ms)
