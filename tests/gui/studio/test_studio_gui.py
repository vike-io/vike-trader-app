import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets  # noqa: E402

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


def test_trades_table_readonly_and_no_navigation(app):
    """Trades table is read-only / row-select with NO row-click navigation (chart focus lives in
    the Chart space now), and selecting a row leaves the results tab unchanged."""
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(_GOOD)
    tab.run_code()
    trades = tab.results._trades
    assert trades.editTriggers() == QtWidgets.QAbstractItemView.NoEditTriggers
    assert trades.selectionBehavior() == QtWidgets.QAbstractItemView.SelectRows
    tab.results._tabs.setCurrentIndex(2)            # stay on the Trades tab
    if trades.rowCount():
        trades.selectRow(0)                          # selecting a row must not navigate
    assert tab.results._tabs.currentIndex() == 2


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
    from vike_trader_app.ui.studio import BacktestConfig, BacktestConfigDialog
    dlg = BacktestConfigDialog(_bars(), capital=5000.0)
    bc = dlg.values()
    assert isinstance(bc, BacktestConfig)
    assert bc.capital == 5000.0
    assert bc.start_ts <= bc.end_ts
    assert bc.resolution_ms is None             # default == base (1m) -> no resample


def test_backtest_config_resolution_resamples(app):
    from vike_trader_app.ui.studio import BacktestConfigDialog
    dlg = BacktestConfigDialog(_bars(), capital=5000.0)
    dlg.resolution.setValue("1H")               # coarser than the 1m base
    assert dlg.values().resolution_ms == 3_600_000   # the coarse window to aggregate to


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
    assert tab.results._tabs.count() == 12           # …|Robustness|Monte Carlo|Periods|Benchmark|WF Matrix|Surface
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


def test_by_symbol_tab_has_6_columns_with_maxdd_and_sharpe(app):
    """F3: portfolio By Symbol tab must have 6 columns; Max DD and Sharpe are non-empty for
    a symbol that traded."""
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import Strategy
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.tester.config import TesterConfig
    from vike_trader_app.ui.studio import StudioTab

    class BuyHold(Strategy):
        def on_bar(self, bar):
            if self.position.size == 0:
                self.buy(1.0)

    # 10 bars so there are enough data points for Sharpe computation (needs >= 2 returns)
    a = [Bar(ts=i, open=10.0, high=11.0, low=9.0, close=10.0 + i) for i in range(10)]
    b = [Bar(ts=i, open=5.0, high=6.0, low=4.0, close=5.0 + i) for i in range(10)]
    report = MultiSymbolStrategyRunner(BuyHold, {"A": a, "B": b}, TesterConfig(cash=1000.0)).report()
    tab = StudioTab()
    tab.show_portfolio_report(report, "DS")

    tbl = tab.results._by_symbol_table
    # 6 columns: Symbol, Trades, Win %, PnL, Max DD, Sharpe
    assert tbl.columnCount() == 6

    rows = tbl.rowCount()
    assert rows == 2  # two symbols

    for r in range(rows):
        sym = tbl.item(r, 0).text()
        # A traded; B may or may not have traded — we check "A" specifically
        if sym == "A":
            maxdd_text = tbl.item(r, 4).text()
            sharpe_text = tbl.item(r, 5).text()
            assert maxdd_text != "—", f"Max DD for {sym} should be computed, got '—'"
            assert sharpe_text != "—", f"Sharpe for {sym} should be computed, got '—'"


# ---------------------------------------------------------------------------
# Portfolio-optimize routing tests
# ---------------------------------------------------------------------------


def _portfolio_bars(n=130):
    """Two symbols with >=120 bars each for the portfolio walk-forward test."""
    import math
    return {
        "AAA": [Bar(ts=i * 86_400_000,
                    open=100 + 10 * math.sin(i / 8.0),
                    high=102 + 10 * math.sin(i / 8.0),
                    low=98 + 10 * math.sin(i / 8.0),
                    close=100 + 10 * math.sin(i / 8.0) + i * 0.04)
                for i in range(n)],
        "BBB": [Bar(ts=i * 86_400_000,
                    open=50 + 5 * math.sin(i / 6.0),
                    high=52 + 5 * math.sin(i / 6.0),
                    low=48 + 5 * math.sin(i / 6.0),
                    close=50 + 5 * math.sin(i / 6.0) + i * 0.02)
                for i in range(n)],
    }


def test_show_portfolio_report_enters_portfolio_mode(app):
    """show_portfolio_report with bars_by_symbol stashes portfolio state."""
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.tester.config import TesterConfig
    from vike_trader_app.core.strategy import Strategy

    class BuyHold(Strategy):
        def on_bar(self, bar):
            if self.position.size == 0:
                self.buy(1.0)

    bbs = {"A": [Bar(ts=i, open=10.0, high=10, low=10, close=10.0 + i) for i in range(5)]}
    report = MultiSymbolStrategyRunner(BuyHold, bbs, TesterConfig(cash=1000.0)).report()
    tab = StudioTab()
    assert tab._portfolio_bars is None  # initially not in portfolio mode
    tab.show_portfolio_report(report, "MySet", bars_by_symbol=bbs, ranges=None)
    assert tab._portfolio_bars is bbs
    assert tab._portfolio_name == "MySet"
    assert tab._portfolio_ranges is None


def test_show_portfolio_report_without_bars_does_not_enter_portfolio_mode(app):
    """show_portfolio_report without bars_by_symbol leaves _portfolio_bars as None."""
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.tester.config import TesterConfig
    from vike_trader_app.core.strategy import Strategy

    class BuyHold(Strategy):
        def on_bar(self, bar):
            if self.position.size == 0:
                self.buy(1.0)

    bbs = {"A": [Bar(ts=i, open=10.0, high=10, low=10, close=10.0 + i) for i in range(5)]}
    report = MultiSymbolStrategyRunner(BuyHold, bbs, TesterConfig(cash=1000.0)).report()
    tab = StudioTab()
    tab.show_portfolio_report(report, "X")  # no bars_by_symbol
    assert tab._portfolio_bars is None      # did not enter portfolio mode


def test_run_code_exits_portfolio_mode(app):
    """run_code (single-symbol) clears _portfolio_bars regardless of prior portfolio state."""
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.tester.config import TesterConfig
    from vike_trader_app.core.strategy import Strategy

    class BuyHold(Strategy):
        def on_bar(self, bar):
            if self.position.size == 0:
                self.buy(1.0)

    bbs = {"A": [Bar(ts=i, open=10.0, high=10, low=10, close=10.0 + i) for i in range(5)]}
    report = MultiSymbolStrategyRunner(BuyHold, bbs, TesterConfig(cash=1000.0)).report()

    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(_GOOD)
    # Put tab into portfolio mode
    tab.show_portfolio_report(report, "DS", bars_by_symbol=bbs)
    assert tab._portfolio_bars is not None
    # Single-symbol run should exit portfolio mode
    tab.run_code()
    assert tab._portfolio_bars is None


def test_optimize_routes_to_portfolio_path(app, monkeypatch):
    """When _portfolio_bars is set, _optimize uses PortfolioStrategyTester (verified via monkeypatch)."""
    from vike_trader_app.analysis.strategy_templates import TEMPLATES

    called_with = {}

    import vike_trader_app.tester.portfolio_tester as pt_mod

    original_cls = pt_mod.PortfolioStrategyTester

    class _Spy(original_cls):
        def walk_forward(self, *args, **kwargs):
            called_with["called"] = True
            return super().walk_forward(*args, **kwargs)

    monkeypatch.setattr(pt_mod, "PortfolioStrategyTester", _Spy)

    # Use a known-good template that has PARAM_GRID + make (and passes the loader validator)
    ma = next(t for t in TEMPLATES if "MaCrossover" in t.code)
    bbs = _portfolio_bars(130)

    tab = StudioTab()
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(ma.code)
    tab._portfolio_bars = bbs
    tab._portfolio_ranges = None
    tab._portfolio_name = "TestDS"
    tab._optimize()

    assert called_with.get("called"), "PortfolioStrategyTester.walk_forward was not called"
    assert tab.results.last_report is not None
    v = tab.results.last_report.verdict
    assert v is not None and v.level in ("Low", "Medium", "High")


def test_optimize_portfolio_result_has_verdict(app):
    """The report stored after a portfolio optimize carries a WF overfit verdict."""
    from vike_trader_app.analysis.strategy_templates import TEMPLATES

    ma = next(t for t in TEMPLATES if "MaCrossover" in t.code)
    bbs = _portfolio_bars(130)

    tab = StudioTab()
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(ma.code)
    tab._portfolio_bars = bbs
    tab._portfolio_ranges = None
    tab._portfolio_name = "TestDS2"
    tab._optimize()
    assert tab.results.last_report is not None
    assert tab.results.last_report.verdict is not None


def test_optimize_single_symbol_path_unchanged(app):
    """When _portfolio_bars is None, _optimize uses the single-symbol StrategyTester path."""
    import math
    from vike_trader_app.analysis.strategy_templates import TEMPLATES

    bars = [Bar(ts=i * 60_000, open=100 + 10 * math.sin(i / 9.0),
                high=102 + 10 * math.sin(i / 9.0), low=98 + 10 * math.sin(i / 9.0),
                close=100 + 10 * math.sin(i / 9.0) + i * 0.04) for i in range(260)]
    ma = next(t for t in TEMPLATES if "MaCrossover" in t.code)
    tab = StudioTab()
    tab.set_bars(bars)
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(ma.code)
    assert tab._portfolio_bars is None   # confirm not in portfolio mode
    tab._optimize()
    assert tab.results.last_report is not None
    assert tab.results.last_report.verdict is not None


def test_optimize_portfolio_too_few_bars_shows_toast(app):
    """Portfolio optimize with <120 bars per symbol shows a toast instead of running."""
    from vike_trader_app.analysis.strategy_templates import TEMPLATES

    # Only 10 bars per symbol - well below the 120-bar minimum
    bbs = {
        "A": [Bar(ts=i * 86_400_000, open=10.0, high=10, low=10, close=10.0 + i) for i in range(10)],
        "B": [Bar(ts=i * 86_400_000, open=5.0, high=5, low=5, close=5.0 + i) for i in range(10)],
    }
    ma = next(t for t in TEMPLATES if "MaCrossover" in t.code)
    tab = StudioTab()
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(ma.code)
    tab._portfolio_bars = bbs
    tab._portfolio_name = "SmallDS"
    runs_before = len(tab.results._runs)
    tab._optimize()
    # A toast was shown but no run recorded
    assert len(tab.results._runs) == runs_before
    assert "120" in tab.results._status.text()  # the "need >=120 bars" toast text was set


# --- AI provider: Cerebras key persistence + Connect-button scoping -----------------------------

def test_cerebras_key_persists_on_keystroke_and_restores(app, tmp_path, monkeypatch):
    """The Cerebras key must persist as you TYPE — editingFinished alone lost it if you quit (or
    clicked Run) before defocusing the field, which wiped the stored key (the reported bug)."""
    s = QtCore.QSettings(str(tmp_path / "ai.ini"), QtCore.QSettings.IniFormat)
    monkeypatch.setattr(StudioTab, "_ai_settings", lambda self: s)

    tab = StudioTab()
    tab.chat.set_provider("cerebras")
    # Type a key WITHOUT firing editingFinished (textChanged only) — the old code never saved this.
    tab.chat._key_input.setText("csk-abc123")
    QtWidgets.QApplication.processEvents()
    assert s.value("ai/cerebras_key") == "csk-abc123"

    # A fresh tab restores the key from settings into the field.
    tab2 = StudioTab()
    assert tab2.chat.cerebras_key() == "csk-abc123"


def test_connect_button_only_for_claude(app):
    """The "🤖 Connect" button installs vike-trader into Claude Desktop/Code over MCP — it applies
    ONLY to the Claude provider, so it must hide for Cerebras (and the key row mirrors it)."""
    tab = StudioTab()

    tab.chat.set_provider("cerebras")
    assert tab.chat._btn_connect.isHidden() is True      # no "Connect to Claude" under Cerebras
    assert tab.chat._key_row.isHidden() is False         # the API-key field is shown instead

    tab.chat.set_provider("claude")
    assert tab.chat._btn_connect.isHidden() is False     # Connect returns for Claude
    assert tab.chat._key_row.isHidden() is True          # key field hidden

    # The live toggle path (SegmentedControl value change) does the same.
    tab.chat._on_provider("cerebras")
    assert tab.chat._btn_connect.isHidden() is True
