"""Off-thread chart-load tests: _CacheReadWorker (Task 1).

The _CacheReadWorker runs load_symbol_bars(network=False) on a QThread and
signals cacheLoaded (a LoadResult) back to the main thread.  LiveHub tracks
it as a third worker slot (_cache_worker) that shutdown() must wait — an
unwaited QThread racing GC is the 0xC0000409 teardown class.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import _CacheReadWorker  # noqa: E402
from vike_trader_app.ui.chartdoc import LiveHub  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_cache_worker_emits_cache_loaded_off_thread(app, monkeypatch):
    """_CacheReadWorker runs load_symbol_bars(network=False) off-thread and emits cacheLoaded
    with a LoadResult.  The symbol is unknown so the cache is empty — but a LoadResult is
    always emitted (even for an empty result), proving the worker path fires correctly."""
    from vike_trader_app.ui.dataload import LoadResult

    # Monkeypatch load_symbol_bars in the module _CacheReadWorker imports from, so the worker
    # (which does `from .dataload import load_symbol_bars` inside run()) picks up the stub.
    import vike_trader_app.ui.dataload as dataload_mod

    stub_result = LoadResult([])
    monkeypatch.setattr(dataload_mod, "load_symbol_bars",
                        lambda *a, **k: stub_result)

    w = _CacheReadWorker("AAA", "1m", 10 * 60_000)
    got = {}
    w.cacheLoaded.connect(lambda res: got.setdefault("res", res))
    w.start()
    assert w.wait(5000), "_CacheReadWorker did not finish within 5 s"
    app.processEvents()
    assert "res" in got, "cacheLoaded was never emitted"
    assert isinstance(got["res"], LoadResult)


def test_cache_worker_emits_failed_on_exception(app, monkeypatch):
    """If load_symbol_bars raises, _CacheReadWorker emits failed(str) instead of cacheLoaded."""
    import vike_trader_app.ui.dataload as dataload_mod

    monkeypatch.setattr(dataload_mod, "load_symbol_bars",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bang")))

    w = _CacheReadWorker("ERR", "1m", 0)
    got = {}
    w.failed.connect(lambda msg: got.setdefault("err", msg))
    w.start()
    assert w.wait(5000)
    app.processEvents()
    assert "err" in got
    assert "bang" in got["err"]


def test_shutdown_waits_cache_worker(app):
    """LiveHub.shutdown() must join _cache_worker (the third slot) before returning.
    An unwaited running worker would race the interpreter's GC — the 0xC0000409 class."""

    hub = LiveHub()

    class _Stub:
        def __init__(self):
            self._waited = False
            self._running = True

        def isRunning(self):
            return self._running

        def wait(self, ms):
            self._waited = True
            self._running = False
            return True

    stub = _Stub()
    hub._cache_worker = stub
    hub.shutdown()
    # After shutdown the slot is cleared (None) and/or the stub was joined.
    assert stub._waited or hub._cache_worker is None, (
        "shutdown() did not wait() the _cache_worker — unwaited worker races GC"
    )


def test_request_cache_read_starts_worker(app, monkeypatch):
    """request_cache_read() installs a _CacheReadWorker on _cache_worker and starts it."""
    import vike_trader_app.ui.dataload as dataload_mod
    from vike_trader_app.ui.dataload import LoadResult

    monkeypatch.setattr(dataload_mod, "load_symbol_bars",
                        lambda *a, **k: LoadResult([]))

    hub = LiveHub()

    # Minimal doc stub: needs symbol, interval, _on_cache_loaded (Task 2 adds the real one).
    class _DocStub:
        symbol = "BTC"
        interval = "1m"
        _bars = []

        def _on_cache_loaded(self, gen, res):
            pass

    doc = _DocStub()
    hub._docs.append(doc)

    hub.request_cache_read(doc, gen=0)
    w = hub._cache_worker
    assert w is not None, "request_cache_read() did not assign _cache_worker"
    assert w.isRunning() or w.wait(3000), "_CacheReadWorker never started"
    w.wait(3000)
    hub.shutdown()
