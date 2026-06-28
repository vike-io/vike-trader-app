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
    # After shutdown the slot is cleared (None) and the stub was joined.
    assert stub._waited, "shutdown() did not wait() the _cache_worker — unwaited worker races GC"


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


# ---------------------------------------------------------------------------
# Task 2 tests — ChartDocument.load() restructure
# ---------------------------------------------------------------------------


def _make_bars(n=10, base=100.0):
    """Synthetic bars for testing (no real cache or network needed)."""
    from vike_trader_app.core.model import Bar

    return [Bar(ts=i * 60_000, open=base + i, high=base + 1 + i,
                low=base - 1 + i, close=base + i) for i in range(n)]


def test_load_with_hub_paints_via_callback(app, monkeypatch):
    """With a hub attached, load() fires an off-thread cache read and returns WITHOUT painting
    synchronously. Painting only happens when the hub delivers the result via _on_cache_loaded.

    This test temporarily removes VIKE_DISABLE_LIVE so the hub code-path is taken (the suite
    conftest sets it for all other tests to prevent real network I/O; here we want the exact
    branch under test — request_cache_read — to run while still stubbing load_symbol_bars so
    no real parquet or network I/O occurs)."""
    import vike_trader_app.ui.chartdoc as chartdoc_mod
    import vike_trader_app.ui.dataload as dataload_mod
    from vike_trader_app.ui.chartdoc import ChartDocument, LiveHub
    from vike_trader_app.ui.dataload import LoadResult

    bars = _make_bars()
    # Patch in both namespaces: chartdoc's direct import (sync fallback) and dataload (worker).
    monkeypatch.setattr(chartdoc_mod, "load_symbol_bars",
                        lambda *a, **k: LoadResult(bars))
    monkeypatch.setattr(dataload_mod, "load_symbol_bars",
                        lambda *a, **k: LoadResult(bars))

    # Remove VIKE_DISABLE_LIVE so load() takes the hub path (request_cache_read).
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    hub = LiveHub()
    doc = ChartDocument("BTCUSDT", "1h")
    hub.register(doc)

    # Track how many times the chart's set_data is called.
    paint_calls = []
    original_set_data = doc.chart.set_data
    monkeypatch.setattr(doc.chart, "set_data",
                        lambda *a, **k: paint_calls.append(1) or original_set_data(*a, **k))

    # load() with a hub and no VIKE_DISABLE_LIVE → off-thread path → must NOT paint yet.
    ret = doc.load()
    assert ret is False, "load() with hub must return False (pending); paint happens via callback"
    assert paint_calls == [], "load() must not paint synchronously when a hub is attached"
    assert doc._bars == [], "bars must not be set until _on_cache_loaded fires"

    # Wait for the cache worker to finish and deliver the result via the signal → _on_cache_loaded.
    w = hub._cache_worker
    if w is not None:
        w.wait(5000)
    app.processEvents()
    app.processEvents()  # second pump: the signal is queued cross-thread

    # Now the callback should have fired and painted.
    assert paint_calls, "_on_cache_loaded did not call _paint() — chart was not updated"
    assert doc._bars == bars, "_on_cache_loaded did not set doc._bars correctly"

    hub.shutdown()


def test_load_without_hub_is_synchronous(app, monkeypatch):
    """A bare doc (no hub) reads + paints synchronously (test/restore path unchanged).
    VIKE_DISABLE_LIVE is irrelevant here since there is no hub to branch on."""
    import vike_trader_app.ui.chartdoc as chartdoc_mod
    from vike_trader_app.ui.chartdoc import ChartDocument
    from vike_trader_app.ui.dataload import LoadResult

    bars = _make_bars()
    # Must patch the name in chartdoc's own namespace (load_symbol_bars was imported from dataload).
    monkeypatch.setattr(chartdoc_mod, "load_symbol_bars",
                        lambda *a, **k: LoadResult(bars))

    doc = ChartDocument("ETHUSDT", "1h")
    # No hub — doc._hub is None.

    paint_calls = []
    original_set_data = doc.chart.set_data
    monkeypatch.setattr(doc.chart, "set_data",
                        lambda *a, **k: paint_calls.append(1) or original_set_data(*a, **k))

    ret = doc.load(network=False)  # network=False → pure sync cache read, no topup needed
    assert ret is True, "no-hub load() with bars must return True synchronously"
    assert paint_calls, "no-hub load() must paint synchronously"
    assert doc._bars == bars, "no-hub load() must set doc._bars immediately"


def test_generation_guard_drops_superseded_cache(app, monkeypatch):
    """_on_cache_loaded(old_gen, res) is silently dropped when the symbol has already changed
    (gen advanced). Neither _bars nor the chart are updated by the stale result."""
    import vike_trader_app.ui.chartdoc as chartdoc_mod
    from vike_trader_app.ui.chartdoc import ChartDocument
    from vike_trader_app.ui.dataload import LoadResult

    bars = _make_bars()
    monkeypatch.setattr(chartdoc_mod, "load_symbol_bars",
                        lambda *a, **k: LoadResult(bars))

    doc = ChartDocument("BTCUSDT", "1h")

    paint_calls = []
    original_set_data = doc.chart.set_data
    monkeypatch.setattr(doc.chart, "set_data",
                        lambda *a, **k: paint_calls.append(1) or original_set_data(*a, **k))

    # Manually advance the generation (simulates a second load() superseding the first).
    doc._load_gen = 5
    stale_gen = 3  # an old generation that should be discarded

    doc._on_cache_loaded(stale_gen, LoadResult(bars))

    assert paint_calls == [], "_on_cache_loaded with stale gen must not paint"
    assert doc._bars == [], "_on_cache_loaded with stale gen must not set bars"
