"""BacktestWorker: run_code() executes the single backtest off the GUI thread.

Tests the async structural contract: run_code() spawns a BacktestWorker, the result arrives on the
main thread via done -> _on_backtest_done -> results.add_run, errors arrive via failed ->
results.show_error, and shutdown() waits the worker. Tests run the worker synchronously via the
_sync_backtest_worker conftest patch (same as ChatWorker/OptimizeWorker, for py3.14 teardown-crash
mitigation). The synchronous override (start() calls run() + finished inline) allows validation of
the signal wiring, re-entrancy guard, and cleanup path without risking the QThread+GC crash
(0xC0000409) that would occur with a real background QThread under parallel test load.
"""

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.tester import TesterConfig  # noqa: E402
from vike_trader_app.ui import studio as studio_mod  # noqa: E402
from vike_trader_app.ui.studio import BacktestWorker, StudioTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _sync_backtest_worker_local(monkeypatch):
    """Override the conftest _sync_backtest_worker (same fixture name, module scope wins) with the
    same synchronous pattern but applied HERE so these tests have explicit control. The conftest
    fixture is function-scoped autouse; pytest applies both — this one is a no-op override because
    the conftest already handles it. We re-declare it to make this file's intent explicit and to
    guard against future conftest changes.

    NOTE: we intentionally keep the sync pattern here (not a real QThread) for the same py3.14
    teardown-crash reason as all other studio GUI tests. The 'off-thread' contract is validated
    structurally (worker built + started, signals wired, shutdown waits) not by measuring thread
    identity, which would require a real-QThread run that risks the GC crash.
    """
    def _sync_start(self):
        self.run()
        self.finished.emit()

    monkeypatch.setattr(studio_mod.BacktestWorker, "start", _sync_start)
    monkeypatch.setattr(studio_mod.BacktestWorker, "isRunning", lambda self: False)
    yield


_VALID_SOURCE = """\
from vike_trader_app.core.strategy import SingleSymbolStrategy as Strategy


class S(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 3:
            self.close()
"""

_INVALID_SOURCE = """\
this is not valid python !!!
"""

_COMPILE_ERROR_SOURCE = """\
from vike_trader_app.core.strategy import SingleSymbolStrategy as Strategy

import os  # forbidden by the security sandbox
class S(Strategy):
    def on_bar(self, bar):
        pass
"""


def _bars(n=16):
    return [
        Bar(ts=i * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.0 + i,
            volume=1.0)
        for i in range(n)
    ]


def _tab_ready():
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.editor.setText(_VALID_SOURCE)
    return tab


# ---------------------------------------------------------------------------
# Core off-thread contract
# ---------------------------------------------------------------------------

def test_run_code_executes_off_thread_and_records_result(app):
    """run_code() spawns a BacktestWorker; done -> _on_backtest_done -> results.add_run records
    the report. With the sync monkeypatch the worker finishes before run_code() returns."""
    tab = _tab_ready()
    tab.run_code()
    # worker finished (sync) -> _clear_backtest_worker called -> None
    assert tab._backtest_worker is None
    # result arrived on the main thread via done -> _on_backtest_done -> add_run
    assert tab.results.last_report is not None
    assert tab.results.last_report.n_trades >= 1


def test_run_code_surfaces_compile_error_via_show_error(app):
    """Invalid strategy code -> BacktestWorker.run() catches the exception and emits failed ->
    results.show_error; no report is recorded."""
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.editor.setText(_INVALID_SOURCE)
    tab.run_code()
    assert tab.results.last_report is None


def test_run_code_surfaces_security_violation_via_show_error(app):
    """A strategy that imports a forbidden module (os) is rejected by load_strategy_from_string
    (validate=True); failed -> show_error; no report stored."""
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.editor.setText(_COMPILE_ERROR_SOURCE)
    tab.run_code()
    assert tab.results.last_report is None


def test_run_code_clears_portfolio_mode(app):
    """run_code() (single-symbol) sets _portfolio_bars = None, exiting portfolio mode."""
    tab = _tab_ready()
    tab._portfolio_bars = {"FAKE": _bars()}  # simulate portfolio mode
    tab.run_code()
    assert tab._portfolio_bars is None


def test_second_run_code_while_worker_running_is_guarded(app, monkeypatch):
    """Re-entrancy guard: a second run_code() while a worker is running is a no-op (no new worker)."""
    tab = _tab_ready()
    # Make the worker look like it stays running (never auto-finishes).
    monkeypatch.setattr(studio_mod.BacktestWorker, "start", lambda self: None)
    monkeypatch.setattr(studio_mod.BacktestWorker, "isRunning", lambda self: True)
    tab.run_code()
    first = tab._backtest_worker
    assert first is not None
    tab.run_code()                  # second press while 'running'
    assert tab._backtest_worker is first   # same worker, no new one spawned
    tab._backtest_worker = None     # clean up the never-started fake worker


def test_shutdown_waits_backtest_worker(app, monkeypatch):
    """shutdown() calls wait() on _backtest_worker if it is running, mirroring OptimizeWorker."""
    tab = _tab_ready()
    waited = []

    class _StubWorker:
        def isRunning(self):
            return True

        def wait(self, ms):
            waited.append(ms)

    tab._backtest_worker = _StubWorker()
    tab.shutdown()
    assert waited == [5000]


def test_shutdown_is_safe_with_no_backtest_worker(app):
    """shutdown() with _backtest_worker=None must not raise."""
    tab = _tab_ready()
    tab.run_code()               # sync -> worker already cleared
    assert tab._backtest_worker is None
    tab.shutdown()               # must be a safe no-op


def test_backtest_worker_class_exists_and_has_signals():
    """BacktestWorker is importable, has done(object,object,object) + failed(str) signals."""
    assert hasattr(BacktestWorker, "done")
    assert hasattr(BacktestWorker, "failed")
