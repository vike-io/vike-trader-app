# tests/gui/exec/test_live_feed_worker.py
"""LiveBarFeedWorker: runs the async bar-feed off the Qt main thread; marshals closed bars
via a queued barClosed signal; stops+waits cleanly (0xC0000409 teardown rule)."""

import asyncio
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui.live_feed_worker import LiveBarFeedWorker  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _FakeFeed:
    """Stand-in for LiveBarFeed: emit two closed bars then idle until stop() returns True.

    run_forever is `async def` to match the REAL LiveBarFeed.run_forever signature exactly.
    """

    def __init__(self):
        self.bars = [
            Bar(ts=i, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)
            for i in (1, 2)
        ]

    async def run_forever(self, on_bar, *, stop=None, max_backoff: float = 30.0):
        for b in self.bars:
            on_bar(b)
        # Idle until stopped — exercises the stop-predicate path without blocking forever.
        while not (stop is not None and stop()):
            await asyncio.sleep(0.01)


def test_worker_emits_closed_bars_then_stops(app):
    """Two bars arrive via barClosed; stop()+wait() tears down cleanly."""
    feed = _FakeFeed()
    w = LiveBarFeedWorker(feed)
    got = []
    w.barClosed.connect(lambda b: got.append(b))
    w.start()

    # Poll until both bars have arrived (signal is queued cross-thread).
    deadline = 3000  # ms
    elapsed = 0
    step = 20
    while len(got) < 2 and elapsed < deadline:
        app.processEvents()
        QtWidgets.QApplication.processEvents()
        import time
        time.sleep(step / 1000)
        elapsed += step

    assert len(got) >= 2, f"expected 2 bars via barClosed, got {len(got)}"

    w.stop()  # sets stop flag + wait()
    assert not w.isRunning(), "worker must not be running after stop()+wait()"
    assert len(got) == 2


def test_worker_stop_is_idempotent(app):
    """Calling stop() on an already-stopped worker must not raise."""
    feed = _FakeFeed()
    w = LiveBarFeedWorker(feed)
    w.start()
    w.stop()
    w.stop()  # second call must be safe
    assert not w.isRunning()


def test_worker_never_started_stop_is_safe(app):
    """stop() on a never-started worker must not raise."""
    feed = _FakeFeed()
    w = LiveBarFeedWorker(feed)
    w.stop()  # must not raise
    assert not w.isRunning()
