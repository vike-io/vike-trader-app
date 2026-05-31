"""The vike-trader-app desktop app: a visual backtester in the vike.io look.

Dockable layout (QDockWidget): Markets + Strategy on the left, the candle/equity
charts and replay bar in the centre, Backtest Report + Trades on the right, with a
full-width header. The "⚠ Validate" button runs the anti-overfit report and lights
up the verdict banner — the differentiator.
"""

import time
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from ..analysis import metrics
from ..core.engine import BacktestEngine
from ..core.paper import PaperTester, pump
from ..core.strategy_loader import load_strategy_from_file
from ..data.binance_source import interval_ms
from ..data.cache import get_bars
from ..data.polling_feed import PollingBarFeed
from ..data.sources import select_source
from ..data.store import RunRecord, Store
from . import theme
from .chart import EquityChart, PriceChart
from .dialogs import LoadDataDialog, default_strategy_factory
from .panels import (
    HistoryPanel,
    ReportPanel,
    StrategyPanel,
    TradesTable,
    WatchlistPanel,
    strategy_params,
)
from .replay import Replay
from .studio import StudioTab
from .journal import JournalTab
from .screener import ScreenerTab
from .tools import ToolsTab

_SPEEDS = [1, 2, 5, 10, 25, 50]  # bars advanced per timer tick
_DAY_MS = 86_400_000
_WATCHLIST_DAYS = 7  # history pulled when clicking a watchlist symbol
_DB_PATH = "storage/db/vike_trader_app.sqlite"
_FORWARD_SEED_BARS = 250  # warm-up history pulled before a forward run starts
_FORWARD_FEE = 0.001
_FORWARD_CASH = 10_000.0


class _LiveFeedWorker(QtCore.QThread):
    """Runs a LiveBarFeed's async loop off the UI thread; marshals bars back via a signal."""

    barReceived = QtCore.Signal(object)  # Bar
    failed = QtCore.Signal(str)

    def __init__(self, feed):
        super().__init__()
        self._feed = feed
        self._stop = False

    def run(self):
        import asyncio

        try:
            asyncio.run(self._feed.run_forever(self.barReceived.emit, stop=lambda: self._stop))
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI thread
            self.failed.emit(str(exc))

    def stop(self):
        self._stop = True


class MainWindow(QtWidgets.QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("vike-trader-app — visual backtester")
        self.resize(1440, 900)
        self.setDockNestingEnabled(True)
        self.setDockOptions(
            QtWidgets.QMainWindow.AnimatedDocks | QtWidgets.QMainWindow.AllowNestedDocks
        )

        self._bars = []
        self._result = None
        self._replay = Replay(0)
        self._strategy_factory = default_strategy_factory()
        self._symbol = "BTCUSDT"
        self._interval = "1m"

        # forward (paper) mode state
        self._forward = None      # PaperTester while live, else None
        self._feed = None         # PollingBarFeed (poll fallback)
        self._fwd_worker = None   # _LiveFeedWorker (push, preferred)
        self._fwd_bars = []       # live bars received this run (charted)

        # widgets
        self.price = PriceChart()
        self.equity = EquityChart()
        self.report = ReportPanel()
        self.trades = TradesTable()
        self.watchlist = WatchlistPanel()
        self.strategy = StrategyPanel()
        self.history = HistoryPanel()
        self.store = Store(str(Path(_DB_PATH)))
        self.watchlist.symbolChosen.connect(self._load_symbol)
        self.history.runChosen.connect(self._open_run)

        self.setMenuWidget(self._build_header())
        self._build_central()
        self._build_docks()
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.strategy.show_strategy(self._strategy_factory)
        self.history.update_runs(self.store.list_runs())

        self._fwd_timer = QtCore.QTimer(self)
        self._fwd_timer.timeout.connect(self._forward_poll_tick)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._on_tick)
        self._clock = QtCore.QTimer(self)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start(1000)
        self._tick_clock()

    # --- header ---
    def _build_header(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setStyleSheet(f"background:{theme.PANEL};border-bottom:1px solid {theme.BORDER};")
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(12, 7, 12, 7)
        row.setSpacing(14)

        mark = QtWidgets.QLabel("V")
        mark.setFixedSize(25, 25)
        mark.setAlignment(QtCore.Qt.AlignCenter)
        mark.setStyleSheet(
            f"background:{theme.ACCENT};color:{theme.BG};font-weight:700;border-radius:6px;"
        )
        name = QtWidgets.QLabel("vike-trader-app")
        name.setStyleSheet("font-size:15px;font-weight:700;")
        tag = QtWidgets.QLabel("BACKTESTER")
        tag.setStyleSheet(
            f"color:{theme.ACCENT};font-size:9px;letter-spacing:2px;"
            f"border:1px solid rgba(255,106,0,0.4);border-radius:4px;padding:2px 6px;"
        )
        self.crumb = QtWidgets.QLabel("No data loaded")
        self.crumb.setStyleSheet(f"color:{theme.TEXT2};")

        feed = QtWidgets.QLabel("● BINANCE")
        feed.setStyleSheet(
            f"color:{theme.TEXT2};font-size:10px;background:{theme.BG};"
            f"border:1px solid {theme.BORDER};border-radius:20px;padding:4px 10px;"
        )
        self.clock = QtWidgets.QLabel("--:--:--")
        self.clock.setStyleSheet(f"color:{theme.TEXT2};")

        for w in (mark, name, tag, self.crumb):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(feed)
        row.addWidget(self.clock)
        return bar

    # --- central charts + replay ---
    def _build_central(self):
        charts = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        charts.addWidget(self.price)
        charts.addWidget(self.equity)
        charts.setStretchFactor(0, 3)
        charts.setStretchFactor(1, 1)

        container = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(container)
        outer.setContentsMargins(7, 7, 7, 7)
        outer.setSpacing(7)
        outer.addWidget(charts, 1)
        outer.addWidget(self._build_controls())

        # The Backtester (charts + replay) and the Studio (AI strategy dev) are sibling
        # tabs of one window — the Studio reuses the same charts/tester under the hood.
        self._backtester = container
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(container, "Backtester")
        self.studio = StudioTab()
        self._wire_studio_agent()
        self.tabs.addTab(self.studio, "Studio")
        self.tools = ToolsTab()
        self.tabs.addTab(self.tools, "Tools")
        self.screener = ScreenerTab()
        self.tabs.addTab(self.screener, "Screener")
        self.journal = JournalTab()
        self.tabs.addTab(self.journal, "Journal")
        self.setCentralWidget(self.tabs)

    def _build_controls(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setStyleSheet(
            f"background:{theme.PANEL};border:1px solid {theme.BORDER};border-radius:8px;"
        )
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(9, 7, 9, 7)
        row.setSpacing(8)

        self.btn_load = QtWidgets.QPushButton("⤓ Load data")
        self.btn_strategy = QtWidgets.QPushButton("⟐ Load strategy")
        self.btn_validate = QtWidgets.QPushButton("⚠ Validate")
        self.btn_validate.setObjectName("validate")
        self.btn_optimize = QtWidgets.QPushButton("⚙ Optimize")
        self.btn_back = QtWidgets.QPushButton("◀")
        self.btn_play = QtWidgets.QPushButton("▶ Play")
        self.btn_play.setObjectName("play")
        self.btn_fwd = QtWidgets.QPushButton("▶|")
        self.btn_full = QtWidgets.QPushButton("⤒ End")
        self.btn_forward = QtWidgets.QPushButton("● Forward (paper)")
        self.btn_forward.setObjectName("forward")
        self.btn_forward.setToolTip("Paper-trade the current strategy on live bars (no real orders)")
        self.btn_load.clicked.connect(self._open_load_dialog)
        self.btn_strategy.clicked.connect(self._load_strategy)
        self.btn_validate.clicked.connect(self._validate)
        self.btn_optimize.clicked.connect(self._optimize)
        self.btn_back.clicked.connect(self._step_back)
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_fwd.clicked.connect(self._step_fwd)
        self.btn_full.clicked.connect(self._jump_end)
        self.btn_forward.clicked.connect(self._toggle_forward)

        self.speed = QtWidgets.QComboBox()
        for s in _SPEEDS:
            self.speed.addItem(f"{s}×", s)
        self.speed.setCurrentIndex(3)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.valueChanged.connect(self._on_slider)
        self.pos_label = QtWidgets.QLabel("bar 0 / 0")
        self.pos_label.setStyleSheet(f"color:{theme.TEXT2};")

        widgets = [
            self.btn_load,
            self.btn_strategy,
            self.btn_validate,
            self.btn_optimize,
            self._sep(),
            self.btn_back,
            self.btn_play,
            self.btn_fwd,
            self.btn_full,
            self._sep(),
            self.btn_forward,
            self._sep(),
        ]
        for w in widgets:
            row.addWidget(w)
        row.addWidget(self.speed)
        row.addWidget(self.slider, 1)
        row.addWidget(self.pos_label)
        return bar

    def _sep(self):
        line = QtWidgets.QFrame()
        line.setFixedWidth(1)
        line.setStyleSheet(f"background:{theme.BORDER};")
        return line

    # --- docks ---
    def _dock(self, title, widget):
        d = QtWidgets.QDockWidget(title.upper(), self)
        d.setFeatures(
            QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFloatable
        )
        d.setWidget(widget)
        return d

    def _build_docks(self):
        markets = self._dock("Markets", self.watchlist)
        strat = self._dock("Strategy", self.strategy)
        report = self._dock("Backtest Report", self._scroll(self.report))
        trades = self._dock("Trades", self.trades)
        history = self._dock("History", self.history)

        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, markets)
        self.splitDockWidget(markets, strat, QtCore.Qt.Vertical)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, report)
        self.splitDockWidget(report, trades, QtCore.Qt.Vertical)
        self.tabifyDockWidget(trades, history)
        trades.raise_()
        self.resizeDocks([markets, report], [262, 380], QtCore.Qt.Horizontal)
        self._docks = [markets, strat, report, trades, history]

    def _scroll(self, widget):
        sc = QtWidgets.QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QtWidgets.QFrame.NoFrame)
        sc.setWidget(widget)
        return sc

    def _on_tab_changed(self, _index: int) -> None:
        """Show the Backtester docks only on the Backtester tab (Studio/Tools are full-width)."""
        on_backtester = self.tabs.currentWidget() is self._backtester
        for d in self._docks:
            d.setVisible(on_backtester)

    def _wire_studio_agent(self) -> None:
        """Give the Studio a live Claude client iff an API key + the [ai] extra are present.

        No key -> the Studio's AI chat stays in the graceful 'No AI client configured' mode
        (and we avoid importing anthropic on every launch).
        """
        import os

        if not os.environ.get("ANTHROPIC_API_KEY"):
            return
        try:
            from ..ai.llm import ClaudeClient

            self.studio.set_agent_client(ClaudeClient())
        except Exception:  # noqa: BLE001 - missing [ai] extra / bad key -> stay in no-AI mode
            pass

    # --- data / strategy loading ---
    def load_bars(self, bars, strategy_factory=None, *, record=True):
        if strategy_factory is not None:
            self._strategy_factory = strategy_factory
        self.strategy.show_strategy(self._strategy_factory)
        self._bars = bars
        self.studio.set_bars(bars)  # the Studio tab backtests the same data
        self._result = BacktestEngine(bars, self._strategy_factory()).run()
        self._replay = Replay(len(bars))
        self.price.set_data(bars, self._result.trades)
        self.price.set_overlays(self._strategy_factory().chart_overlays([b.close for b in bars]))
        self.equity.set_data(self._result.equity_curve)
        self.report.update_stats(self._result)
        self.trades.update_trades(self._result.trades)
        self.report.verdict.setVisible(False)
        self.slider.setMaximum(self._replay.last_index)
        self.slider.setValue(self._replay.last_index)
        if bars:
            last = bars[-1].close
            self.crumb.setText(
                f"{self._symbol}  ·  {self._interval}  ·  {last:,.2f}  ·  {len(bars):,} bars"
            )
        self._render_frame()
        if record and bars:
            self._save_run()

    def _save_run(self):
        """Persist the just-finished backtest to the history store."""
        if not self._bars or self._result is None:
            return
        eq = self._result.equity_curve
        rec = RunRecord(
            ts=int(time.time() * 1000),
            symbol=self._symbol,
            interval=self._interval,
            strategy=self._strategy_factory.__name__,
            start_ts=self._bars[0].ts,
            end_ts=self._bars[-1].ts,
            n_bars=len(self._bars),
            net_return=metrics.total_return(eq),
            final_equity=self._result.final_equity,
            trades=len(self._result.trades),
            win_rate=metrics.win_rate(self._result.trades),
            profit_factor=metrics.profit_factor(self._result.trades),
            max_drawdown=metrics.max_drawdown(eq),
            sharpe=metrics.sharpe(eq),
            params=strategy_params(self._strategy_factory),
        )
        self.store.save_run(rec)
        self.history.update_runs(self.store.list_runs())

    def _open_run(self, rec):
        """Reopen a past run: reload its exact data window from cache and re-run."""
        self.crumb.setText(f"Reopening {rec.symbol} {rec.interval}…")
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            bars = get_bars(rec.symbol, rec.interval, rec.start_ts, rec.end_ts,
                            fetcher=select_source(rec.symbol).fetch_bars_range)
        except Exception as exc:  # noqa: BLE001 - report load failure
            QtWidgets.QMessageBox.warning(self, "Reopen failed", f"{rec.symbol}: {exc}")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._symbol = rec.symbol
        self._interval = rec.interval
        self.load_bars(bars, record=False)

    def _open_load_dialog(self):
        dlg = LoadDataDialog(self)
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.bars:
            self._symbol = dlg.symbol.text().strip() or self._symbol
            self._interval = dlg.interval.currentText()
            self.load_bars(dlg.bars)

    def _load_symbol(self, symbol):
        """Fetch a symbol from Binance (cached) and run the current strategy on it."""
        self.crumb.setText(f"Loading {symbol}…")
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        now = int(time.time() * 1000)
        start = now - _WATCHLIST_DAYS * _DAY_MS
        try:
            bars = get_bars(symbol, "1m", start, now, progress=self._fetch_progress,
                            fetcher=select_source(symbol).fetch_bars_range)
        except Exception as exc:  # noqa: BLE001 - report network/load failure
            QtWidgets.QMessageBox.warning(self, "Load failed", f"{symbol}: {exc}")
            self.crumb.setText("No data loaded")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._symbol = symbol
        self._interval = "1m"
        self.load_bars(bars)

    def _fetch_progress(self, done, start, end):
        pct = (done - start) / max(end - start, 1) * 100
        self.crumb.setText(f"Loading {self._symbol}…  {pct:.0f}%")
        QtWidgets.QApplication.processEvents()

    def _load_strategy(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load strategy (.py)", "", "Python (*.py)"
        )
        if not path:
            return
        try:
            self._strategy_factory = load_strategy_from_file(path)
        except Exception as exc:  # noqa: BLE001 - report load failure to the user
            QtWidgets.QMessageBox.critical(self, "Load failed", str(exc))
            return
        self.strategy.show_strategy(self._strategy_factory)
        if self._bars:
            self.load_bars(self._bars)

    def _optimize(self):
        if not self._bars:
            QtWidgets.QMessageBox.information(self, "Optimize", "Load data first.")
            return
        from .optimizer import show_optimizer

        show_optimizer(self, self._bars, self._strategy_factory, fee_rate=0.001)

    def _validate(self):
        if not self._bars:
            QtWidgets.QMessageBox.information(self, "Validate", "Load data first.")
            return
        grid = getattr(self._strategy_factory, "PARAM_GRID", {})
        if not grid:
            QtWidgets.QMessageBox.information(
                self,
                "Validate",
                f"{self._strategy_factory.__name__} declares no PARAM_GRID, so there is "
                "nothing to optimize. Add a PARAM_GRID to enable anti-overfit checks.",
            )
            return
        self.btn_validate.setEnabled(False)
        self.crumb.setText("Validating (optimizing + anti-overfit)…")
        QtWidgets.QApplication.processEvents()
        try:
            from ..analysis.report import build_overfit_report

            report = build_overfit_report(
                self._bars, self._strategy_factory.make, grid, n_splits=4, fee_rate=0.001
            )
            self.report.show_verdict(report)
        finally:
            self.btn_validate.setEnabled(True)
            if self._bars:
                last = self._bars[-1].close
                self.crumb.setText(
                    f"{self._symbol}  ·  1m  ·  {last:,.2f}  ·  {len(self._bars)} bars"
                )

    # --- replay wiring ---
    def _render_frame(self):
        i = self._replay.index
        self.price.show_upto(i)
        self.equity.show_upto(i)
        self.pos_label.setText(f"bar {i} / {self._replay.last_index}")
        if self.slider.value() != i:
            self.slider.blockSignals(True)
            self.slider.setValue(i)
            self.slider.blockSignals(False)

    def _on_tick(self):
        for _ in range(self.speed.currentData()):
            self._replay.tick()
        if not self._replay.playing:
            self._timer.stop()
            self.btn_play.setText("▶ Play")
        self._render_frame()

    def _toggle_play(self):
        if self._replay.playing:
            self._replay.pause()
            self._timer.stop()
            self.btn_play.setText("▶ Play")
        else:
            if self._replay.at_end:
                self._replay.seek(0)
            self._replay.play()
            self._timer.start()
            self.btn_play.setText("⏸ Pause")

    def _step_fwd(self):
        self._replay.step()
        self._render_frame()

    def _step_back(self):
        self._replay.step_back()
        self._render_frame()

    def _jump_end(self):
        self._replay.seek(self._replay.last_index)
        self._render_frame()

    def _on_slider(self, value):
        self._replay.seek(value)
        self._render_frame()

    # --- forward (paper) mode ---
    def _toggle_forward(self):
        if self._forward is not None:
            self._stop_forward()
        else:
            self._start_forward()

    def _start_forward(self):
        """Seed warm-up history, then stream live closed bars into a PaperTester."""
        symbol, interval = self._symbol, self._interval
        src = select_source(symbol)  # crypto -> vike/binance; forex -> Yahoo+Dukascopy
        self.crumb.setText(f"Forward: seeding {symbol} {interval}…")
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            now = int(time.time() * 1000)
            start = now - _FORWARD_SEED_BARS * interval_ms(interval)
            seed = src.fetch_bars_range(symbol, interval, start, now)
        except Exception as exc:  # noqa: BLE001 - network/seed failure
            QtWidgets.QMessageBox.warning(self, "Forward failed", f"Could not seed {symbol}: {exc}")
            self.crumb.setText("No data loaded")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self._forward = PaperTester(
            symbol=symbol, interval=interval, strategy=self._strategy_factory(),
            cash=_FORWARD_CASH, fee_rate=_FORWARD_FEE, seed_bars=seed,
            store=self.store, on_step=None, created_ts=int(time.time() * 1000),
        )
        self._fwd_bars = []
        self._set_backtest_controls_enabled(False)
        self.btn_forward.setText("■ Stop forward")

        # Prefer the push WebSocket feed (lower latency) when the source has one;
        # forex has no push feed, so it falls straight through to REST polling.
        if not (src.supports_live_ws and self._start_live_worker(symbol, interval)):
            self._feed = PollingBarFeed(
                symbol, interval, fetch_latest=src.make_fetch_latest(symbol, interval)
            )
            self._fwd_timer.start(int(self._feed.poll_seconds * 1000))
        self.crumb.setText(f"● FORWARD (paper) · {symbol} · {interval} · waiting for bars…")

    def _start_live_worker(self, symbol, interval) -> bool:
        """Start a LiveBarFeed in a QThread. Returns False if [live]/websockets is unavailable."""
        try:
            from ..data.vike_live import make_live_feed

            import websockets  # noqa: F401 - probe the optional [live] dep before threading
        except Exception:  # noqa: BLE001 - websockets not installed -> caller polls instead
            return False
        worker = self._fwd_worker = _LiveFeedWorker(make_live_feed(symbol, interval))
        worker.barReceived.connect(self._on_forward_bar)
        worker.failed.connect(self._on_forward_failed)
        worker.start()
        return True

    def _forward_poll_tick(self):
        """REST-polling fallback: drain newly-closed bars into the tester, then repaint."""
        if self._forward is None or self._feed is None:
            return
        if pump(self._feed, self._forward):
            self._render_forward()

    def _on_forward_bar(self, bar):
        """Slot for a bar pushed from the LiveBarFeed worker thread."""
        if self._forward is None:
            return
        self._forward.on_bar_live(bar)
        self._render_forward()

    def _on_forward_failed(self, message):
        QtWidgets.QMessageBox.warning(self, "Forward feed error", message)
        self._stop_forward()

    def _render_forward(self):
        """Repaint charts/panels from the live tester state (live bars only)."""
        if self._forward is None:
            return
        self._fwd_bars = list(self._forward.engine.bars[-len(self._forward.equity_curve):]) \
            if self._forward.equity_curve else []
        res = self._forward.result()
        self.price.set_data(self._fwd_bars, res.trades)
        self.price.set_overlays(
            self._strategy_factory().chart_overlays([b.close for b in self._fwd_bars])
        )
        self.equity.set_data(res.equity_curve)
        self.price.show_upto(len(self._fwd_bars) - 1)
        self.equity.show_upto(len(res.equity_curve) - 1)
        self.report.update_stats(res)
        self.trades.update_trades(res.trades)
        if self._fwd_bars:
            last = self._fwd_bars[-1].close
            self.crumb.setText(
                f"● FORWARD (paper) · {self._symbol} · {self._interval} · "
                f"{last:,.2f} · {len(self._fwd_bars)} live bars · eq {res.final_equity:,.0f}"
            )

    def _stop_forward(self):
        self._fwd_timer.stop()
        if self._fwd_worker is not None:
            self._fwd_worker.stop()
            self._fwd_worker.wait(2000)
            self._fwd_worker = None
        if self._forward is not None:
            self._forward.stop()
        self._forward = None
        self._feed = None
        self.btn_forward.setText("● Forward (paper)")
        self._set_backtest_controls_enabled(True)

    def _set_backtest_controls_enabled(self, on: bool):
        """Lock backtest/replay controls while forward mode owns the charts (and vice-versa)."""
        for w in (
            self.btn_load, self.btn_strategy, self.btn_validate, self.btn_optimize,
            self.btn_back, self.btn_play, self.btn_fwd, self.btn_full, self.slider, self.speed,
        ):
            w.setEnabled(on)

    def closeEvent(self, event):  # noqa: N802 - Qt override
        self._stop_forward()  # never leave a feed thread running
        self.studio.shutdown()  # wait out any in-flight AI worker (no destroyed-while-running)
        super().closeEvent(event)

    def _tick_clock(self):
        self.clock.setText(QtCore.QTime.currentTime().toString("HH:mm:ss"))


def main():
    import sys

    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(theme.stylesheet())
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
