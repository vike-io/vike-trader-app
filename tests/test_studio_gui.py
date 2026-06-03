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


def test_studio_layout_reports_top_editor_chat_bottom(app):
    # New layout: results/chart tab strip on top; AI-studio chat | editor as two cards below.
    tab = StudioTab()
    assert tab._vsplit.count() == 2                       # [results, bottom-row]
    assert tab._vsplit.widget(0) is tab.results          # reports + chart tabs on top
    assert tab._bottom.count() == 2                       # chat | editor, two half-width cards
    assert tab._bottom.widget(0) is tab.chat             # AI Studio chat is the left card


def test_chat_without_client_is_graceful(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.chat.promptSubmitted.emit("make me a strategy")
    # no client set -> no crash (a system message is appended)


def test_run_populates_equity_and_trades(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(_GOOD)
    tab.run_code()
    # equity tab is populated after the run
    assert tab.results._equity is not None
    assert tab.results.last_report is not None
    assert tab.results.last_report.equity_curve  # non-empty equity curve stored
    # trades table + linkage list got the round-trips
    assert tab.results._trades.rowCount() == tab.results.last_report.n_trades
    assert len(tab.results._report_trades) == tab.results.last_report.n_trades


def test_trade_click_is_noop(app):
    """_on_trade_clicked is now a deliberate no-op; price-chart focus lives in the Chart space."""
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(_GOOD)
    tab.run_code()
    tab.results._tabs.setCurrentIndex(2)        # stay on the Trades tab
    result = tab.results._on_trade_clicked(0, 0)  # call must not raise
    assert result is None                          # returns nothing (explicit return)
    assert tab.results._tabs.currentIndex() == 2  # tab index unchanged — no navigation


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
    cap, start_ts, end_ts, res_ms = dlg.values()
    assert cap == 5000.0
    assert start_ts <= end_ts
    assert res_ms is None                       # default == base (1m) -> no resample


def test_backtest_config_resolution_resamples(app):
    from vike_trader_app.ui.studio import BacktestConfigDialog
    dlg = BacktestConfigDialog(_bars(), capital=5000.0)
    dlg.resolution.setValue("1H")               # coarser than the 1m base
    _cap, _s, _e, res_ms = dlg.values()
    assert res_ms == 3_600_000                  # returns the coarse window to aggregate to


def test_segmented_control_disables_finer_options(app):
    from vike_trader_app.ui.studio import BacktestConfigDialog
    dlg = BacktestConfigDialog(_bars())          # 1m base
    # finer-than-base options don't exist below 1m here, but 1m must be enabled + selectable
    assert dlg.resolution._buttons["1m"].isEnabled()
    assert dlg.resolution.value() == "1m"


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


def test_template_dialog_builds_and_autoselects(app):
    from vike_trader_app.ui.templates import StrategyTemplateDialog
    dlg = StrategyTemplateDialog()
    assert dlg._code.toPlainText() != ""   # first template auto-selected
    assert dlg._btn_load.isEnabled()


def test_load_template_into_empty_editor_runs(app):
    from vike_trader_app.analysis.strategy_templates import TEMPLATES
    tab = StudioTab()
    tab._load_template(TEMPLATES[0].code)          # empty editor -> loads without a prompt
    assert "class MaCrossover" in tab.text()
    tab.set_bars(_bars(60))
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.run_code()
    assert tab.results.last_report is not None     # the loaded template actually runs


def test_distribution_tab_and_mfe_mae_columns(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(_GOOD)
    tab.run_code()
    assert tab.results._tabs.count() == 6            # Equity|Performance|Trades|By Symbol|Runs|Distribution
    assert tab.results._trades.columnCount() == 9    # ... + MFE + MAE columns
    assert tab.results._dist is not None


def test_export_csv_without_report_is_graceful(app):
    tab = StudioTab()
    tab._export_csv()                                # no report -> toast, no dialog, no crash
    assert tab.results.last_report is None


def test_optimize_walk_forward_attaches_overfit_verdict(app):
    import math

    from vike_trader_app.analysis.strategy_templates import TEMPLATES
    bars = [Bar(ts=i * 60_000, open=100 + 10 * math.sin(i / 9.0),
                high=102 + 10 * math.sin(i / 9.0), low=98 + 10 * math.sin(i / 9.0),
                close=100 + 10 * math.sin(i / 9.0) + i * 0.04) for i in range(260)]
    ma = next(t for t in TEMPLATES if "MaCrossover" in t.code)  # has a PARAM_GRID
    tab = StudioTab()
    tab.set_bars(bars)
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(ma.code)
    tab._optimize()
    assert tab.results.last_report is not None
    v = tab.results.last_report.verdict
    assert v is not None and v.level in ("Low", "Medium", "High")  # the PBO/overfit gate


def test_optimize_without_grid_is_graceful(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_text(_GOOD)        # no PARAM_GRID
    tab._optimize()            # -> toast, no run recorded, no crash
    assert tab.results.last_report is None


def test_indicator_dialog_builds_and_autoselects(app):
    from vike_trader_app.ui.indicators import IndicatorCatalogDialog
    dlg = IndicatorCatalogDialog()
    assert dlg._code.toPlainText() != ""   # first indicator auto-selected
    assert dlg._btn_insert.isEnabled()


def test_indicator_insert_appends_to_editor(app):
    from vike_trader_app.analysis.indicator_catalog import CATALOG
    tab = StudioTab()
    tab.set_text("# my strategy\n")
    tab._insert_snippet(CATALOG[0].snippet)
    assert CATALOG[0].snippet.strip() in tab.text()
    assert "# my strategy" in tab.text()  # existing code preserved


def test_concurrent_prompt_is_refused(app):
    tab = StudioTab()
    tab.set_agent_client(object())       # any non-None client

    class _Busy:
        def isRunning(self):
            return True

    tab._worker = _Busy()
    tab._on_prompt("second prompt")      # a worker is "running" -> must not start another
    assert isinstance(tab._worker, _Busy)  # unchanged; the submit was refused


def test_current_strategy_cls_none_for_empty_editor(app):
    from vike_trader_app.ui.studio import StudioTab
    tab = StudioTab()
    tab.editor.setText("")
    assert tab.current_strategy_cls() is None


def test_current_strategy_cls_compiles_template(app):
    from vike_trader_app.analysis.strategy_templates import TEMPLATES
    from vike_trader_app.ui.studio import StudioTab
    tab = StudioTab()
    tab.editor.setText(TEMPLATES[0].code)
    cls = tab.current_strategy_cls()
    assert cls is not None and callable(cls)


def test_show_portfolio_report_displays(app):
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import Strategy
    from vike_trader_app.tester.config import TesterConfig
    from vike_trader_app.ui.studio import StudioTab

    class BuyHold(Strategy):
        def on_bar(self, bar):
            if self.position.size == 0:
                self.buy(1.0)

    bars = {"A": [Bar(ts=i, open=10.0, high=10, low=10, close=10.0 + i) for i in range(5)]}
    report = MultiSymbolStrategyRunner(BuyHold, bars, TesterConfig(cash=1000.0)).report()
    tab = StudioTab()
    tab.show_portfolio_report(report, "MySet")   # must not raise
    assert tab.results.last_report is not None


def test_by_symbol_tab_populates_for_portfolio_report(app):
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import Strategy
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.tester.config import TesterConfig
    from vike_trader_app.ui.studio import StudioTab

    class BuyHold(Strategy):
        def on_bar(self, bar):
            if self.position.size == 0:
                self.buy(1.0)

    a = [Bar(ts=i, open=10.0, high=10, low=10, close=10.0 + i) for i in range(5)]
    b = [Bar(ts=i, open=5.0, high=5, low=5, close=5.0 + i) for i in range(5)]
    report = MultiSymbolStrategyRunner(BuyHold, {"A": a, "B": b}, TesterConfig(cash=1000.0)).report()
    tab = StudioTab()
    tab.show_portfolio_report(report, "DS")
    # the By Symbol table exists and has one row per symbol in per_symbol_pnl
    rows = tab.results._by_symbol_table.rowCount()
    assert rows == len(report.per_symbol_pnl)
    syms = {tab.results._by_symbol_table.item(r, 0).text() for r in range(rows)}
    assert syms == set(report.per_symbol_pnl)


def test_by_symbol_tab_empty_for_single_symbol_run(app):
    from vike_trader_app.analysis.strategy_templates import TEMPLATES
    from vike_trader_app.core.model import Bar
    from vike_trader_app.ui.studio import StudioTab

    tab = StudioTab()
    tab.editor.setText(TEMPLATES[0].code)
    tab.set_bars([Bar(ts=i * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.0 + i)
                  for i in range(60)])
    tab.run_code()                       # single-symbol run -> per_symbol_pnl is None
    assert tab.results._by_symbol_table.rowCount() == 0
