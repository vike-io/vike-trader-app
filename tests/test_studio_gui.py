import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.tester import TesterConfig  # noqa: E402
from vike_trader_app.ui.editor import CodeEditor  # noqa: E402
from vike_trader_app.ui.studio import StudioTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bars(n=12):
    return [Bar(ts=i * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.0 + i)
            for i in range(n)]


_GOOD = """
from vike_trader_app.core.strategy import Strategy

class S(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 3:
            self.close()
"""

_BAD = "this is not valid python ((("


def test_code_editor_basic(app):
    ed = CodeEditor()
    ed.setText("x = 1\ny = 2\n")
    assert "x = 1" in ed.text()
    assert ed.line_number_area_width() > 0


def test_studio_run_good_strategy(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(_GOOD)
    tab.run_code()
    assert tab.results.last_report is not None
    assert tab.results.last_report.n_trades >= 1


def test_studio_run_bad_strategy_shows_error(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_text(_BAD)
    tab.run_code()   # must NOT raise
    # error surfaced, no report stored
    assert getattr(tab.results, "last_report", None) is None


def test_studio_has_three_panes(app):
    tab = StudioTab()
    splitters = tab.findChildren(QtWidgets.QSplitter)
    assert splitters and splitters[0].count() == 3


def test_chat_without_client_is_graceful(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.chat.promptSubmitted.emit("make me a strategy")
    # no client set -> no crash (a system message is appended)


def test_run_populates_chart_and_trades(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(_GOOD)
    tab.run_code()
    # chart got the bars; trades table + linkage list got the round-trips
    assert tab.results._price._bars
    assert tab.results._trades.rowCount() == tab.results.last_report.n_trades
    assert len(tab.results._report_trades) == tab.results.last_report.n_trades


def test_trade_click_jumps_to_chart(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(_GOOD)
    tab.run_code()
    tab.results._tabs.setCurrentIndex(2)        # look at the Trades tab
    tab.results._on_trade_clicked(0, 0)         # click the first trade
    assert tab.results._tabs.currentIndex() == 0  # jumped to the Chart tab
    assert tab.results._price._follow is False    # chart focused on that trade


def test_runs_history_records_and_reopens(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(_GOOD)
    tab.run_code()
    tab.run_code()
    assert len(tab.results._runs) == 2
    assert tab.results._runs_table.rowCount() == 2
    tab.results._on_run_clicked(0, 0)            # reopening a past run must not raise
    assert tab.results.last_report is not None


def test_backtest_config_dialog_values(app):
    from vike_trader_app.ui.studio import BacktestConfigDialog
    dlg = BacktestConfigDialog(_bars(), capital=5000.0)
    cap, start_ts, end_ts = dlg.values()
    assert cap == 5000.0
    assert start_ts <= end_ts


def test_run_respects_capital_override(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(_GOOD)
    tab._run_capital = 5000.0                     # as the Settings modal would set
    tab.run_code()
    assert abs(tab.results.last_report.equity_curve[0] - 5000.0) < 1.0


def test_diff_dialog_builds_and_highlights(app):
    from vike_trader_app.ui.studio import DiffDialog
    DiffDialog("a = 1\nb = 2\n", "a = 1\nb = 3\nc = 4\n", version=1)  # constructs
    left, right = DiffDialog._diff_html("a = 1\nb = 2", "a = 1\nb = 3")
    assert "background" in right and "b = 3" in right  # changed/added line highlighted


def test_pct_money_handle_inf_nan(app):
    rp = StudioTab().results
    assert rp._pct(float("inf")) == "∞"
    assert rp._pct(float("nan")) == "—"
    assert rp._money(float("inf")) == ""
    assert rp._money(float("nan")) == ""


def test_short_span_run_does_not_crash(app):
    # 2 bars with a gain -> tiny time-span -> annualized **(1/years) would OverflowError unguarded
    bars = [Bar(ts=0, open=100.0, high=100.0, low=100.0, close=100.0),
            Bar(ts=60_000, open=100.0, high=120.0, low=100.0, close=120.0)]
    tab = StudioTab()
    tab.set_bars(bars)
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(_GOOD)
    tab.run_code()                       # must not raise
    if tab.results._runs:
        tab.results._on_run_clicked(0, 0)  # the previously-uncaught Qt-slot path must not raise


def test_concurrent_prompt_is_refused(app):
    tab = StudioTab()
    tab.set_agent_client(object())       # any non-None client

    class _Busy:
        def isRunning(self):
            return True

    tab._worker = _Busy()
    tab._on_prompt("second prompt")      # a worker is "running" -> must not start another
    assert isinstance(tab._worker, _Busy)  # unchanged; the submit was refused
