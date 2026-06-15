"""Offscreen tests for the Studio optimizer UI: WF matrix tab, surface tab, optimizer config dialog."""

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.core.strategy import Strategy  # noqa: E402
from vike_trader_app.tester import StrategyTester, TesterConfig  # noqa: E402
from vike_trader_app.ui.studio import OptimizerConfigDialog, ResultsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Sma(Strategy):
    fast = 2
    slow = 3
    PARAM_GRID = {"fast": [2, 3], "slow": [3, 4, 5]}

    def __init__(self):
        super().__init__()
        self._c = []

    def on_bar(self, bar):
        self._c.append(bar.close)
        if len(self._c) < self.slow:
            return
        f = sum(self._c[-self.fast:]) / self.fast
        s = sum(self._c[-self.slow:]) / self.slow
        if f > s and self.position.size == 0:
            self.buy(1.0)
        elif f < s and self.position.size != 0:
            self.close()


def _make(**p):
    return _Sma.make(**p)


def _bars(n=160):
    out = []
    for i in range(n):
        c = 100.0 + 5.0 * math.sin(i / 5.0) + 0.03 * i
        out.append(Bar(ts=i * 60_000, open=c, high=c + 0.5, low=c - 0.5, close=c, volume=1.0))
    return out


def _walk_forward():
    st = StrategyTester(_make, _bars(160), TesterConfig(taker_fee=0.0))
    return st, st.walk_forward(_make, _Sma.PARAM_GRID, n_splits=3, criterion="total_return")


def test_wf_matrix_populates_and_marks_pass_fail(app):
    _st, wf = _walk_forward()
    panel = ResultsPanel()
    panel.show_walk_forward(wf, "total_return")
    assert panel._wf_table.rowCount() == wf.n_windows
    results = {panel._wf_table.item(r, 6).text() for r in range(panel._wf_table.rowCount())}
    assert results and results <= {"PASS", "FAIL"}
    assert "Windows:" in panel._wf_summary.text()
    assert "WF efficiency:" in panel._wf_summary.text()


def test_surface_populates_axes_and_renders_2d(app):
    st, _ = _walk_forward()
    rep = st.optimize(_make, _Sma.PARAM_GRID, criterion="total_return", method="grid")
    panel = ResultsPanel()
    panel.show_surface(rep.ranked, _Sma.PARAM_GRID, "total_return")
    # both multi-valued params offered as axes; offscreen always shows the 2D fallback
    assert panel._surface_x.count() == 2
    assert panel._surface_y.count() == 2
    assert panel._surface_stack.currentWidget() is panel._surface_img


def test_surface_needs_two_params_shows_hint(app):
    st = StrategyTester(_make, _bars(160), TesterConfig(taker_fee=0.0))
    rep = st.optimize(_make, {"fast": [2, 3]}, criterion="total_return")
    panel = ResultsPanel()
    panel.show_surface(rep.ranked, {"fast": [2, 3]}, "total_return")
    assert panel._surface_x.count() == 0
    assert "≥2" in panel._surface_caption.text()


def test_optimizer_config_dialog_roundtrip_and_contextual_enable(app):
    from vike_trader_app.ui.studio import OptimizerConfig
    cfg = OptimizerConfig(method="genetic", criterion="sortino", mode="rolling", n_splits=5,
                          n_trials=80, pop_size=30, generations=12, sampler="gp", seed=7)
    dlg = OptimizerConfigDialog(cfg)
    assert dlg.values() == cfg
    # genetic -> population/generations enabled, trials/sampler disabled
    assert dlg.pop_size.isEnabled() and dlg.generations.isEnabled()
    assert not dlg.n_trials.isEnabled() and not dlg.sampler.isEnabled()
    dlg.method.setCurrentText("bayesian")
    assert dlg.n_trials.isEnabled() and dlg.sampler.isEnabled()
    assert not dlg.pop_size.isEnabled()


def test_clear_resets_wf_and_surface(app):
    _st, wf = _walk_forward()
    panel = ResultsPanel()
    panel.show_walk_forward(wf, "total_return")
    panel.clear()
    assert panel._wf_table.rowCount() == 0
    assert panel._surface_trials == []


def test_optimizer_dialog_hides_bayesian_without_optuna(app, monkeypatch):
    import vike_trader_app.ui.studio as studio
    monkeypatch.setattr(studio, "_HAS_OPTUNA", False)
    dlg = studio.OptimizerConfigDialog(studio.OptimizerConfig(method="bayesian"))
    methods = [dlg.method.itemText(i) for i in range(dlg.method.count())]
    assert "bayesian" not in methods
    assert dlg.method.currentText() == "grid"  # a requested-but-unavailable method falls back
