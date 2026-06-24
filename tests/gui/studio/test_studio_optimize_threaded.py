"""Studio Walk-forward runs OFF the GUI thread in an ``OptimizeWorker``.

Drives the real ``StudioTab``: pressing Walk-forward builds an ``OptimizeWorker`` (it does NOT block
the GUI on the grid sweep), and when the job finishes the results panels populate, the worker is
released and the button re-enabled. A second press while one is running is guarded, and ``shutdown``
is safe. The worker is run SYNCHRONOUSLY here — a real QThread running backtests + GC can hard-segfault
under full-suite load on py3.14, the same reason the AI ChatWorker test does it (see
test_studio_ai_user_sim.py); the seam's compute is covered by tests/unit/tester/test_optimize_job.py.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.tester import TesterConfig  # noqa: E402
from vike_trader_app.ui import studio as studio_mod  # noqa: E402
from vike_trader_app.ui.studio import OptimizerConfig, StudioTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _sync_opt_worker(monkeypatch):
    """Run the OptimizeWorker synchronously: ``done`` + ``finished`` are delivered on the main thread,
    so the production emit -> _on_optimize_result -> _on_optimize_finished flow runs deterministically
    and ``tab._opt_worker`` is cleared exactly as in production — without a real QThread."""
    def _sync_start(self):
        self.run()              # run_optimize_job + done.emit -> _on_optimize_result (direct)
        self.finished.emit()    # -> _on_optimize_finished -> clears tab._opt_worker, re-enables button
    monkeypatch.setattr(studio_mod.OptimizeWorker, "start", _sync_start)
    monkeypatch.setattr(studio_mod.OptimizeWorker, "isRunning", lambda self: False)
    yield


_SOURCE = '''from vike_trader_app.core.strategy import Strategy


class SmaX(Strategy):
    WARMUP = 30
    fast = 5
    slow = 20
    PARAM_GRID = {"fast": [3, 5, 8], "slow": [15, 20, 30]}

    def __init__(self):
        self.closes = []

    def on_bar(self, bar):
        self.closes.append(bar.close)
        if len(self.closes) <= self.slow:
            return
        f = sum(self.closes[-self.fast:]) / self.fast
        s = sum(self.closes[-self.slow:]) / self.slow
        fp = sum(self.closes[-self.fast - 1:-1]) / self.fast
        sp = sum(self.closes[-self.slow - 1:-1]) / self.slow
        if fp <= sp and f > s and self.position.size == 0:
            self.buy(1.0)
        elif fp >= sp and f < s and self.position.size > 0:
            self.close()
'''

_NO_GRID = '''from vike_trader_app.core.strategy import Strategy


class S(Strategy):
    def on_bar(self, bar):
        pass
'''


def _bars(n=480):
    out = []
    for i in range(n):
        c = round(100 + 25 * math.sin(i / 11.0) + 12 * math.sin(i / 47.0) + i * 0.02, 2)
        out.append(Bar(ts=i * 60_000, open=c, high=c, low=c, close=c, volume=1.0))
    return out


def _tab_ready():
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.editor.setText(_SOURCE)
    # workers=1 keeps the sync run in-process (no worker-pool spawn during the test).
    tab._opt_config = OptimizerConfig(method="grid", criterion="total_return", n_splits=3, workers=1)
    return tab


def test_walk_forward_runs_off_thread_and_populates_results(app):
    tab = _tab_ready()
    tab._optimize()
    # the (synchronous) worker finished -> released + button re-enabled
    assert tab._opt_worker is None
    assert tab._btn_optimize.isEnabled()
    # both results panels populated from the job: WF matrix rows + the surface axes (Part A: the
    # surface sweep ran via the worker pool path, not skipped)
    assert tab.results._wf_table.rowCount() == 3
    assert tab.results._surface_x.count() == 2


def test_optimize_without_param_grid_spawns_nothing(app):
    tab = _tab_ready()
    tab.editor.setText(_NO_GRID)
    tab._optimize()
    assert tab._opt_worker is None        # no grid -> toast + return, no worker built


def test_second_optimize_while_running_is_guarded(app, monkeypatch):
    tab = _tab_ready()
    # Make the worker look like it stays running and do NOT auto-finish it.
    monkeypatch.setattr(studio_mod.OptimizeWorker, "start", lambda self: None)
    monkeypatch.setattr(studio_mod.OptimizeWorker, "isRunning", lambda self: True)
    tab._optimize()
    first = tab._opt_worker
    assert first is not None
    assert not tab._btn_optimize.isEnabled()   # busy state engaged
    tab._optimize()                            # second press while running
    assert tab._opt_worker is first            # guarded: no new worker spawned
    tab._opt_worker = None                     # drop the never-started fake worker before teardown


def test_shutdown_is_safe_with_no_worker(app):
    tab = _tab_ready()
    tab._optimize()          # synchronous -> worker already cleared
    assert tab._opt_worker is None
    tab.shutdown()           # (None, None) -> safe no-op, must not raise
