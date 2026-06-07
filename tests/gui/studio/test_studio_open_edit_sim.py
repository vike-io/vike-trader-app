"""Studio user simulation: OPEN an existing strategy → EDIT it → BACKTEST → FORWARD-test →
view the REPORT → EXPORT it. Complements test_studio_ai_user_sim.py (which covers AI *creation*);
here the user works with the template gallery and the editor the way they would by hand.

Offscreen Qt, no network, main thread only. Modal dialogs are neutralised by monkeypatching exec/
QFileDialog/QMessageBox so nothing blocks headless.
"""

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.analysis.strategy_templates import TEMPLATES  # noqa: E402
from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.core.paper import PaperTester  # noqa: E402
from vike_trader_app.tester import TesterConfig  # noqa: E402
from vike_trader_app.ui.studio import StudioTab  # noqa: E402
from vike_trader_app.ui.templates import StrategyTemplateDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bars(n=240):
    """An oscillating series so MA-crossover / mean-reversion templates actually trade."""
    out = []
    for i in range(n):
        p = 100.0 + 12.0 * math.sin(i / 9.0)
        out.append(Bar(ts=i * 60_000, open=p, high=p + 1.0, low=p - 1.0, close=p))
    return out


def _template(name):
    return next(t for t in TEMPLATES if t.name == name)


def _studio(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    return tab


# --- open an existing (template) strategy -----------------------------------

def test_open_template_into_empty_editor_then_backtest_shows_report(app):
    tab = _studio(app)
    tmpl = _template("MA crossover")
    assert not tab.text().strip()             # empty editor -> no confirm dialog

    tab._load_template(tmpl.code)             # the slot StrategyTemplateDialog.loadRequested fires
    assert tab.text() == tmpl.code            # existing strategy now open in the editor

    tab.run_code()                            # user presses Run
    report = tab.results.last_report
    assert report is not None                 # backtest produced a report -> visible in Results
    assert report.equity_curve                # equity curve populated for the Equity tab
    assert report.n_trades >= 1               # the oscillating series triggers crossovers


def test_load_template_over_existing_code_confirms_then_replaces(app, monkeypatch):
    tab = _studio(app)
    tab.set_text("# my work in progress\n")
    # Non-empty editor -> _load_template asks before clobbering; the user clicks Yes.
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question",
        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes),
    )
    tmpl = _template("RSI mean-reversion")
    tab._load_template(tmpl.code)
    assert tab.text() == tmpl.code

    # And if the user clicks No, the editor is left untouched.
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question",
        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.No),
    )
    tab._load_template(_template("Donchian breakout").code)
    assert tab.text() == tmpl.code            # unchanged


# --- edit the opened strategy and re-run ------------------------------------

def test_edit_loaded_strategy_and_rerun_produces_a_fresh_report(app):
    tab = _studio(app)
    tab._load_template(_template("MA crossover").code)
    tab.run_code()
    assert tab.results.last_report is not None
    runs_before = len(tab.results._runs)

    # User edits the code (append a harmless comment + a tweak) and runs again.
    tab.set_text(tab.text() + "\n# tuned by hand\n")
    tab.run_code()
    assert tab.results.last_report is not None
    assert len(tab.results._runs) == runs_before + 1   # a second run was recorded in history


# --- forward-test the opened strategy ---------------------------------------

def test_forward_test_opened_strategy_grows_equity(app):
    tab = _studio(app)
    tab._load_template(_template("MA crossover").code)
    cls = tab.current_strategy_cls()           # compile the editor's strategy
    assert cls is not None

    bars = _bars()
    ft = PaperTester(symbol="BTCUSDT", interval="1m", strategy=cls(),
                     cash=10_000.0, seed_bars=bars[:30])
    for bar in bars[30:]:
        ft.on_bar_live(bar)
    res = ft.result()
    assert res.equity_curve                     # a live forward equity curve was produced
    assert len(res.equity_curve) == len(bars) - 30


# --- view + export the report -----------------------------------------------

def test_export_report_csv_writes_metrics(app, monkeypatch, tmp_path):
    tab = _studio(app)
    tab._load_template(_template("MA crossover").code)
    tab.run_code()
    assert tab.results.last_report is not None

    out = tmp_path / "report.csv"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "CSV (*.csv)")),
    )
    tab._export_csv()
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert text.strip()                         # non-empty CSV of metrics/trades


# --- the template gallery dialog itself -------------------------------------

def test_template_dialog_lists_templates_and_emits_code(app):
    dlg = StrategyTemplateDialog()
    captured = []
    dlg.loadRequested.connect(captured.append)

    # The dialog should expose every registered template; pick the first and emit its code the way
    # the "Load" action does.
    assert dlg._list.count() == len(TEMPLATES)
    dlg._list.setCurrentRow(0)
    dlg._load()                                # the "Load into editor" button handler
    assert captured and captured[0] == TEMPLATES[0].code
    dlg.deleteLater()
