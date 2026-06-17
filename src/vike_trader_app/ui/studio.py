"""Studio tab: ChatPanel | CodeEditor | ResultsPanel with Run wiring and ChatWorker thread.

The ResultsPanel has five tabs — Equity (stand-alone equity curve), Performance (KPI hero
tiles + detail grid), Trades (round-trips), Runs (iterate-and-compare history), and
Distribution (trade-return histogram). The price candlestick chart now lives in the Chart
space (app.py), not here. The overfit-risk verdict banner sits above the tabs.
"""

import difflib
import html
from dataclasses import asdict, dataclass, replace
from importlib.util import find_spec

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from ..analysis import report_extras
from . import icons, theme
from .chart import EquityChart
from .editor import CodeEditor
from .flowlayout import FlowLayout

# Optional 3D surface render. pyqtgraph.opengl pulls in the whole PyOpenGL stack (OpenGL.GL), ~280ms
# at import — paid by EVERY launch even though the 3D surface is a rarely-opened Studio tab. So only
# DETECT availability here with find_spec (cheap — locates the module without executing it) and DEFER
# the real import to _draw_surface_gl (first 3D draw). Absent / headless / a GL hiccup -> the flat
# pyqtgraph ImageView heatmap fallback, so the Surface tab always works.
_HAS_GL = find_spec("pyqtgraph.opengl") is not None and find_spec("OpenGL") is not None
_gl = None  # the pyqtgraph.opengl module — imported lazily on the first real 3D draw


def _gl_usable() -> bool:
    """3D GL is usable only with PyOpenGL present AND a real (non-offscreen) display."""
    if not _HAS_GL:
        return False
    app = QtGui.QGuiApplication.instance()
    return app is not None and app.platformName() != "offscreen"


# Bayesian optimization needs the optional `[opt]` extra (optuna). Cheap presence check (no import).
import importlib.util as _ilu  # noqa: E402

_HAS_OPTUNA = _ilu.find_spec("optuna") is not None


# Optimizer-config option lists (mirror tester StrategyTester._CRITERIA + samplers methods).
_OPT_METHODS = ["grid", "random", "genetic", "bayesian"]
_OPT_CRITERIA = ["sharpe", "sortino", "calmar", "omega", "total_return", "profit_factor", "recovery_factor"]
_WF_MODES = ["anchored", "rolling"]
_BAYES_SAMPLERS = ["tpe", "gp", "cmaes", "random"]


@dataclass
class OptimizerConfig:
    """Optimizer + walk-forward settings (was a bare 9-key dict). Field names match the tester
    optimize/walk_forward kwargs exactly, so `asdict(cfg)` IS the kwargs dict."""
    method: str = "grid"
    criterion: str = "sharpe"
    mode: str = "anchored"
    n_splits: int = 3
    n_trials: int = 50
    pop_size: int = 20
    generations: int = 10
    sampler: str = "tpe"
    seed: int = 0


@dataclass
class BacktestConfig:
    """One backtest's run settings (was a positional 4-tuple). ``resolution_ms`` is None when the
    chosen resolution equals/exceeds the base data (no resampling)."""
    capital: float
    start_ts: int
    end_ts: int
    resolution_ms: int | None
# Cap the in-sample grid we backtest to draw the optimization surface (combos = product of axes).
_SURFACE_MAX_COMBOS = 400

_YEAR_MS = 365.25 * 24 * 60 * 60 * 1000.0

# Studio spacing tokens — every distance in the Studio layout is ONE of these two values, so the
# whole space reads as uniform. Tune here and the entire layout follows.
#   _GAP — the gap BETWEEN sibling things: outer frame inset, both splitter handles (chat↔editor,
#          results↔cards), grid spacing between KPI tiles, toolbar/input gaps, list spacing.
#   _PAD — the padding INSIDE a card/tile (its border to its content): the panes, the KPI tiles,
#          the example-prompt cards. Kept at 2×_GAP so the two relate cleanly.
_GAP = 6
_PAD = 12
# Smallest a splitter pane may be dragged to before the collapse-to-zero kicks in (keeps the tab
# strip / toolbar visible). Lets the chart be shrunk smoothly instead of snapping shut past ~50%.
_PANE_MIN = 32


def _fmt_param(v) -> str:
    """Compact param value for the matrix (3.0 -> '3', keep bools/strings as-is)."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _range_str(rng) -> str:
    """Render a walk-forward window range — bar-index ('a–b') or epoch-ms ('date→date')."""
    try:
        lo, hi = rng
    except Exception:  # noqa: BLE001
        return "—"
    if lo is None or hi is None:
        return "—"
    if lo >= 10 ** 11:  # epoch milliseconds (portfolio windows are date-based)
        from datetime import datetime, timezone
        def _d(ms):
            return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        return f"{_d(lo)}→{_d(hi)}"
    return f"{int(lo)}–{int(hi)}"


def _readonly_table(labels, *, stretch_all: bool = True, resize: dict | None = None):
    """A no-edit, row-select, alternating-row QTableWidget with the vertical header hidden — the
    shared config every results tab repeated. ``labels`` sets the columns; by default every column
    stretches, and ``resize`` overrides specific ones, e.g. ``{0: QHeaderView.ResizeToContents}``."""
    t = QtWidgets.QTableWidget(0, len(labels))
    t.setHorizontalHeaderLabels(list(labels))
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    t.setAlternatingRowColors(True)
    hdr = t.horizontalHeader()
    if stretch_all:
        hdr.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
    for col, mode in (resize or {}).items():
        hdr.setSectionResizeMode(col, mode)
    return t


# ---------------------------------------------------------------------------
# ResultsPanel
# ---------------------------------------------------------------------------

class ResultsPanel(QtWidgets.QWidget):
    """Tabbed results — Equity | Performance | Trades | Runs | Distribution."""

    # hero tiles (caption); values + $ sub-lines are computed in show_report
    _HERO = [
        ("roi",            "ROI"),
        ("annualized",     "Annualized ROI"),
        ("win_ratio",      "Win Ratio"),
        ("max_drawdown",   "Max Drawdown"),
        ("time_in_market", "Time in Market"),
        ("profit_factor",  "Profit Factor"),
    ]
    # detail grid (plain TesterReport attributes)
    _DETAIL = [
        ("sharpe",          "Sharpe"),
        ("sortino",         "Sortino"),
        ("net_profit",      "Net profit"),
        ("expected_payoff", "Expected payoff"),
        ("recovery_factor", "Recovery factor"),
        ("avg_win",         "Avg win"),
        ("avg_loss",        "Avg loss"),
        ("total_fees",      "Total fees"),
    ]
    _SIGNED = {"net_profit", "expected_payoff", "avg_win"}

    _TRADE_COLS = ["#", "Side", "Entry", "Exit", "Size", "PnL", "Return", "MFE", "MAE"]
    _BY_SYMBOL_COLS = ["Symbol", "Trades", "Win %", "PnL", "Max DD", "Sharpe"]
    _RUN_COLS = ["#", "Return", "Max DD", "Trades", "Sharpe"]

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        # Zero card inset — the StudioTab root margin (the outer frame) and the splitter handles are
        # the ONLY gaps, so every content-to-content distance equals _GAP (see StudioTab for the
        # full model). Card insets here would double up with the handle and break that uniformity.
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(_GAP)

        # verdict banner (above the tabs — always visible when set)
        self._banner = QtWidgets.QLabel()
        self._banner.setVisible(False)
        self._banner.setWordWrap(True)
        root.addWidget(self._banner)

        self._tabs = QtWidgets.QTabWidget()
        # The app-wide `QTabWidget::pane { top:-1px }` pulls the pane (the chart) up 1px so it
        # underlaps the transparent tab bar — the chart grid then pokes up THROUGH the tabs. Pin
        # this pane flush (top:0) + a 1px BORDER separator so the chart sits cleanly below the tabs.
        self._tabs.setStyleSheet(
            f"QTabWidget::pane{{border:none;border-top:1px solid {theme.BORDER};top:0px;}}")
        root.addWidget(self._tabs, 1)

        # status / toast line (errors red, success green) — a floating overlay pinned to the FAR
        # RIGHT of the tab-strip row (the tabs sit on the left, so its right area is free). Being an
        # overlay (not in the layout) it never shifts the tabs/chart down, so the vertical rhythm
        # stays uniform; _position_status keeps it parked top-right on every resize.
        self._status = QtWidgets.QLabel(self)
        self._status.setVisible(False)
        self._status.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self._status.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        self._build_equity_tab()
        self._build_performance_tab()
        self._build_trades_tab()
        self._build_by_symbol_tab()
        self._build_runs_tab()
        self._build_distribution_tab()
        self._build_robustness_tab()
        self._build_montecarlo_tab()
        self._build_periods_tab()
        self._build_benchmark_tab()
        self._build_wf_matrix_tab()
        self._build_surface_tab()

        self.last_report: object = None
        self._report_trades: list = []           # row -> Trade, for the chart-focus linkage
        self._runs: list = []                     # stored runs: {report, bars, overlays}

    # --- tab builders ---

    def _build_equity_tab(self) -> None:
        self._equity = EquityChart()
        self._tabs.addTab(self._equity, "Equity")

    def _make_tile(self, caption: str):
        cell = QtWidgets.QWidget()
        cell.setAttribute(QtCore.Qt.WA_StyledBackground, True)  # paint bg/border on a bare QWidget
        cell.setStyleSheet(
            f"background:{theme.PANEL2};border:1px solid {theme.BORDER};border-radius:10px;"
        )
        cv = QtWidgets.QVBoxLayout(cell)
        cv.setContentsMargins(_PAD, _PAD, _PAD, _PAD)
        cv.setSpacing(3)   # caption→value→sub: tight typographic grouping inside the tile
        cap = QtWidgets.QLabel(caption.upper())
        cap.setStyleSheet(
            f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;border:none;"
        )
        val = QtWidgets.QLabel("—")
        val.setStyleSheet(
            f"color:{theme.TEXT};font-family:{theme.FONT_MONO};font-weight:700;"
            f"font-size:23px;border:none;"
        )
        sub = QtWidgets.QLabel("")
        sub.setStyleSheet(
            f"color:{theme.TEXT2};font-family:{theme.FONT_MONO};font-size:11px;border:none;"
        )
        cv.addWidget(cap)
        cv.addWidget(val)
        cv.addWidget(sub)
        return cell, val, sub

    def _build_performance_tab(self) -> None:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        body = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(body)
        outer.setContentsMargins(_GAP, _GAP, _GAP, _GAP)
        outer.setSpacing(_GAP)

        # hero KPI tiles (2 columns)
        hero = QtWidgets.QGridLayout()
        hero.setHorizontalSpacing(_GAP)
        hero.setVerticalSpacing(_GAP)
        self._hero_val: dict[str, QtWidgets.QLabel] = {}
        self._hero_sub: dict[str, QtWidgets.QLabel] = {}
        for i, (key, label) in enumerate(self._HERO):
            cell, val, sub = self._make_tile(label)
            self._hero_val[key] = val
            self._hero_sub[key] = sub
            hero.addWidget(cell, i // 2, i % 2)
        outer.addLayout(hero)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet(f"color:{theme.BORDER};")
        outer.addWidget(line)

        # detail grid (smaller)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(_GAP)
        grid.setVerticalSpacing(_GAP)
        self._value_labels: dict[str, QtWidgets.QLabel] = {}
        for i, (key, label) in enumerate(self._DETAIL):
            cap = QtWidgets.QLabel(label.upper())
            cap.setStyleSheet(f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;")
            val = QtWidgets.QLabel("—")
            val.setStyleSheet(self._metric_style("", None))
            self._value_labels[key] = val
            r, c = divmod(i, 2)
            holder = QtWidgets.QWidget()
            hv = QtWidgets.QVBoxLayout(holder)
            hv.setContentsMargins(0, 0, 0, 0)
            hv.setSpacing(0)
            hv.addWidget(cap)
            hv.addWidget(val)
            grid.addWidget(holder, r, c)
        outer.addLayout(grid)
        outer.addStretch(1)

        scroll.setWidget(body)
        self._tabs.addTab(scroll, "Performance")

    def _build_trades_tab(self) -> None:
        self._trades = _readonly_table(
            self._TRADE_COLS, resize={0: QtWidgets.QHeaderView.ResizeToContents})
        self._tabs.addTab(self._trades, "Trades")

    def _build_by_symbol_tab(self) -> None:
        self._by_symbol_table = _readonly_table(
            self._BY_SYMBOL_COLS, resize={0: QtWidgets.QHeaderView.ResizeToContents})
        self._tabs.addTab(self._by_symbol_table, "By Symbol")

    def _build_runs_tab(self) -> None:
        self._runs_table = _readonly_table(self._RUN_COLS)
        self._runs_table.cellClicked.connect(self._on_run_clicked)
        self._tabs.addTab(self._runs_table, "Runs")

    def _build_distribution_tab(self) -> None:
        self._dist = pg.PlotWidget()
        self._dist.setBackground(theme.BG)
        self._dist.showGrid(x=True, y=True, alpha=0.12)
        self._dist.getAxis("left").setTextPen(theme.TEXT3)
        self._dist.getAxis("bottom").setTextPen(theme.TEXT3)
        self._dist.getAxis("bottom").enableAutoSIPrefix(False)  # show raw return fractions
        self._dist.setLabel("bottom", "trade return")
        self._dist.setLabel("left", "count")
        self._tabs.addTab(self._dist, "Distribution")

    _METRIC_VALUE_RESIZE = {0: QtWidgets.QHeaderView.ResizeToContents,
                            1: QtWidgets.QHeaderView.Stretch}

    def _build_metric_tab(self, label: str):
        """Build (and tab-add) a 2-column Metric|Value table — robustness / monte-carlo / benchmark
        all share this exact shape; only the tab label and stored attribute differ."""
        table = _readonly_table(
            ["Metric", "Value"], stretch_all=False, resize=self._METRIC_VALUE_RESIZE)
        self._tabs.addTab(table, label)
        return table

    def _build_robustness_tab(self) -> None:
        self._robust_table = self._build_metric_tab("Robustness")

    def _build_montecarlo_tab(self) -> None:
        self._mc_table = self._build_metric_tab("Monte Carlo")

    def _build_periods_tab(self) -> None:
        # Periods tab: a vertical splitter with the monthly heatmap on top and the drawdown table below.
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_GAP)

        self._periods_table = _readonly_table(  # Year + Jan..Dec + Year-total = 14 cols
            ["Year", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Annual"])
        layout.addWidget(self._periods_table, 2)

        dd_label = QtWidgets.QLabel("TOP DRAWDOWNS")
        dd_label.setStyleSheet(
            f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;padding:4px 0 0 0;"
        )
        layout.addWidget(dd_label)

        self._dd_table = _readonly_table(["Depth", "Peak", "Trough", "Length (bars)"])
        layout.addWidget(self._dd_table, 1)

        self._tabs.addTab(container, "Periods")

    def _build_benchmark_tab(self) -> None:
        self._bench_table = self._build_metric_tab("Benchmark")

    _WF_COLS = ["Window", "Train", "Test", "Best params", "IS", "OOS", "Result"]

    def _build_wf_matrix_tab(self) -> None:
        # Walk-forward matrix: one row per IS/OOS window, with PASS/FAIL coloring (green = the
        # window's out-of-sample result is profitable). The summary line carries the headline
        # robustness numbers (efficiency / consistency / mode) that separate a robust system
        # from a curve-fit one — the MultiCharts "Matrix Optimization" view, our way.
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(_GAP, _GAP, _GAP, _GAP)
        layout.setSpacing(_GAP)

        self._wf_summary = QtWidgets.QLabel("Run Walk-forward to populate the matrix.")
        self._wf_summary.setWordWrap(True)
        self._wf_summary.setStyleSheet(f"color:{theme.TEXT2};font-size:12px;")
        layout.addWidget(self._wf_summary)

        self._wf_table = _readonly_table(
            self._WF_COLS, resize={3: QtWidgets.QHeaderView.ResizeToContents})
        layout.addWidget(self._wf_table, 1)
        self._tabs.addTab(container, "WF Matrix")

    def _build_surface_tab(self) -> None:
        # 3D optimization surface (param-x × param-y × metric). Real GLSurfacePlotItem when PyOpenGL
        # is installed on a real display; a flat ImageView heatmap otherwise (headless/CI). The axis
        # pickers re-pivot the SAME stored trials — no recompute — so flipping axes is instant.
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(_GAP, _GAP, _GAP, _GAP)
        layout.setSpacing(_GAP)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(_GAP)
        self._surface_caption = QtWidgets.QLabel("Run Walk-forward on a ≥2-param grid for the surface.")
        self._surface_caption.setStyleSheet(f"color:{theme.TEXT2};font-size:12px;")
        row.addWidget(self._surface_caption)
        row.addStretch(1)

        def _axis_cap(text: str) -> QtWidgets.QLabel:
            lab = QtWidgets.QLabel(text)
            lab.setStyleSheet(f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;")
            return lab

        self._surface_x = QtWidgets.QComboBox()
        self._surface_y = QtWidgets.QComboBox()
        for combo in (self._surface_x, self._surface_y):
            combo.setMinimumWidth(90)
            combo.currentIndexChanged.connect(self._render_surface)
        row.addWidget(_axis_cap("X"))
        row.addWidget(self._surface_x)
        row.addWidget(_axis_cap("Y"))
        row.addWidget(self._surface_y)
        layout.addLayout(row)

        # 2D fallback (always built — works headless). The GL view is built lazily on first render.
        self._surface_img = pg.ImageView()
        self._surface_img.ui.histogram.hide()
        self._surface_img.ui.roiBtn.hide()
        self._surface_img.ui.menuBtn.hide()
        try:
            self._surface_img.setColorMap(pg.colormap.get("inferno"))
        except Exception:  # noqa: BLE001 - colormap name availability varies by pg build
            pass
        self._surface_stack = QtWidgets.QStackedWidget()
        self._surface_stack.addWidget(self._surface_img)  # index 0 = 2D
        self._surface_gl = None  # lazily-built GLViewWidget at index 1
        self._surface_gl_item = None
        layout.addWidget(self._surface_stack, 1)

        self._surface_trials: list = []
        self._surface_grid: dict = {}
        self._surface_criterion = "sharpe"
        self._tabs.addTab(container, "Surface")

    def mount_chart_tab(self, chart: QtWidgets.QWidget) -> None:
        """Add the price chart as a 'Chart' tab after Distribution, so the reports and the chart
        share one top tab strip (Equity | Performance | Trades | Runs | Distribution | Chart)."""
        self._tabs.addTab(chart, "Chart")

    def _update_distribution(self, returns) -> None:
        self._dist.clear()
        edges, counts = report_extras.returns_histogram(returns, bins=20)
        if not counts:
            return
        width = (edges[1] - edges[0]) * 0.9
        for i, h in enumerate(counts):
            cx = (edges[i] + edges[i + 1]) / 2.0
            color = theme.UP if cx >= 0 else theme.DOWN
            self._dist.addItem(pg.BarGraphItem(x=[cx], height=[h], width=width,
                                               brush=pg.mkBrush(color), pen=None))
        self._dist.addLine(x=0.0, pen=pg.mkPen(theme.TEXT3, style=QtCore.Qt.DashLine))

    # --- formatting helpers ---

    @staticmethod
    def _pct(v) -> str:
        if v is None or v != v:            # None or NaN
            return "—"
        if v in (float("inf"), float("-inf")):
            return "∞" if v > 0 else "−∞"
        return f"{v * 100:.2f}%"

    @staticmethod
    def _money(v) -> str:
        if v is None or v != v or v in (float("inf"), float("-inf")):
            return ""
        sign = "+" if v > 0 else "−" if v < 0 else ""
        return f"{sign}${abs(v):,.2f}"

    def _fmt(self, key: str, raw) -> str:
        if raw is None:
            return "—"
        if isinstance(raw, int):
            return str(raw)
        if raw == float("inf"):
            return "∞"
        if raw == float("-inf"):
            return "−∞"
        return f"{raw:,.2f}"

    def _metric_style(self, key: str, raw) -> str:
        base = f"font-family:{theme.FONT_MONO};font-weight:700;font-size:14px;"
        color = theme.TEXT
        if isinstance(raw, (int, float)) and raw == raw:  # exclude NaN
            if key in self._SIGNED:
                color = theme.UP if raw > 0 else theme.DOWN if raw < 0 else theme.TEXT
            elif key == "avg_loss":
                color = theme.DOWN if raw < 0 else theme.TEXT
        return f"color:{color};{base}"

    def _hero_color(self, key: str, raw) -> str:
        good = theme.TEXT
        if isinstance(raw, (int, float)) and raw == raw:
            if key in ("roi", "annualized"):
                good = theme.UP if raw > 0 else theme.DOWN if raw < 0 else theme.TEXT
            elif key == "max_drawdown":
                good = theme.DOWN if raw > 0 else theme.TEXT
            elif key == "profit_factor":
                good = theme.UP if raw >= 1 else theme.DOWN
            elif key == "win_ratio":
                good = theme.UP if raw >= 0.5 else theme.TEXT
        return (f"color:{good};font-family:{theme.FONT_MONO};font-weight:700;"
                f"font-size:23px;border:none;")

    def _set_banner(self, verdict) -> None:
        if verdict is None:
            self._banner.setVisible(False)
            return
        level = verdict.level
        color = theme.VERDICT.get(level, theme.WARN)
        reason = verdict.reasons[0] if verdict.reasons else ""
        self._banner.setText(f"⚠  OVERFIT RISK · {level.upper()}  —  {reason}")
        self._banner.setStyleSheet(
            f"padding:8px 10px;border-radius:6px;font-size:13px;font-weight:700;"
            f"color:{color};background:rgba(0,0,0,0.25);border:1px solid {color};"
        )
        self._banner.setVisible(True)

    # --- derived $/annualized/time-in-market (computed from report + bars) ---

    @staticmethod
    def _derive(report, bars):
        eq = report.equity_curve or []
        initial = eq[0] if eq else None
        roi_dollars = (report.final_equity - initial) if initial is not None else None
        # exact $ drawdown from the equity curve (peak-to-trough)
        dd_dollars, peak = None, float("-inf")
        if eq:
            worst = 0.0
            for v in eq:
                peak = v if v > peak else peak
                worst = max(worst, peak - v)
            dd_dollars = worst
        # annualized ROI from the real bar time-span
        annualized = None
        years = None
        if bars and len(bars) > 1:
            span_ms = bars[-1].ts - bars[0].ts
            years = span_ms / _YEAR_MS if span_ms > 0 else None
            if years and years > 1e-6 and initial and initial > 0 and report.final_equity > 0:
                try:
                    annualized = (report.final_equity / initial) ** (1.0 / years) - 1.0
                except OverflowError:  # sub-day spans -> astronomically large exponent
                    annualized = float("inf")
        # time in market: fraction of the span spent holding a position
        time_in_market = None
        if bars and len(bars) > 1:
            span_ms = bars[-1].ts - bars[0].ts
            if span_ms > 0:
                held = sum(max(0, t.exit_ts - t.entry_ts) for t in report.trades)
                time_in_market = min(1.0, held / span_ms)
        wins = sum(1 for t in report.trades if t.pnl > 0)
        return {
            "roi_dollars": roi_dollars,
            "dd_dollars": dd_dollars,
            "annualized": annualized,
            "time_in_market": time_in_market,
            "wins": wins,
            "n": len(report.trades),
        }

    def _fill_trades(self, trades, mfe_mae=None) -> None:
        self._report_trades = list(trades)
        self._trades.setRowCount(len(trades))
        for r, t in enumerate(trades):
            ret = t.pnl / (abs(t.size) * t.entry_price) if t.size and t.entry_price else 0.0
            mfe, mae = mfe_mae[r] if mfe_mae and r < len(mfe_mae) else (None, None)
            cells = [
                str(r + 1),
                "LONG" if t.size >= 0 else "SHORT",
                f"{t.entry_price:,.2f}",
                f"{t.exit_price:,.2f}",
                f"{abs(t.size):g}",
                f"{t.pnl:,.2f}",
                f"{ret * 100:.2f}%",
                f"{mfe * 100:.2f}%" if mfe is not None else "—",
                f"{mae * 100:.2f}%" if mae is not None else "—",
            ]
            pnl_col = theme.UP if t.pnl >= 0 else theme.DOWN
            colors = {
                1: theme.UP if t.size >= 0 else theme.DOWN,
                5: pnl_col, 6: pnl_col,
                7: theme.UP,    # MFE = best excursion -> green
                8: theme.DOWN,  # MAE = worst excursion -> red
            }
            self._set_row(self._trades, r, cells, colors=colors, align_from=2)

    def _fill_by_symbol(self, report) -> None:
        from ..analysis import metrics as _m
        pnl_map = getattr(report, "per_symbol_pnl", None)
        if not pnl_map:
            self._by_symbol_table.setRowCount(0)
            return
        curves_map = getattr(report, "per_symbol_curves", None) or {}
        # Count trades and wins per symbol
        trades_by_sym: dict[str, int] = {s: 0 for s in pnl_map}
        wins_by_sym: dict[str, int] = {s: 0 for s in pnl_map}
        for t in report.trades:
            sym = getattr(t, "symbol", "")
            if sym in trades_by_sym:
                trades_by_sym[sym] += 1
                if t.pnl > 0:
                    wins_by_sym[sym] += 1
        # Sort rows by PnL descending
        sorted_syms = sorted(pnl_map, key=lambda s: pnl_map[s], reverse=True)
        self._by_symbol_table.setRowCount(len(sorted_syms))
        for r, sym in enumerate(sorted_syms):
            n = trades_by_sym[sym]
            wins = wins_by_sym[sym]
            pnl = pnl_map[sym]
            win_pct = f"{wins / n * 100:.2f}%" if n > 0 else "—"
            up = pnl >= 0
            # Per-symbol Max DD and Sharpe from the cumulative PnL curve (offset by 1.0 so
            # the ratio-based metrics work correctly on an equity-like series).
            curve = curves_map.get(sym)
            if curve and len(curve) >= 2:
                base = 1.0
                eq = [base + v for v in curve]
                mdd_str = self._pct(_m.max_drawdown(eq))
                sharpe_str = f"{_m.sharpe(eq, 252):.2f}"
            else:
                mdd_str = "—"
                sharpe_str = "—"
            cells = [sym, str(n), win_pct, self._money(pnl), mdd_str, sharpe_str]
            self._set_row(self._by_symbol_table, r, cells,
                          colors={3: theme.UP if up else theme.DOWN})

    # --- analytics tab fill helpers ---

    @staticmethod
    def _table_row(table: QtWidgets.QTableWidget, row: int, label: str, value: str,
                   value_color: str | None = None) -> None:
        """Set one key/value row on a two-column QTableWidget."""
        label_item = QtWidgets.QTableWidgetItem(label)
        value_item = QtWidgets.QTableWidgetItem(value)
        value_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        if value_color:
            value_item.setForeground(QtGui.QColor(value_color))
        table.setItem(row, 0, label_item)
        table.setItem(row, 1, value_item)

    def _fill_metric_table(self, table, rows) -> None:
        """Populate a 2-column metric table from ``rows`` of (label, value, color) — the shared tail
        of the robustness / monte-carlo / benchmark fills."""
        table.setRowCount(len(rows))
        for r, (lbl, val, col) in enumerate(rows):
            self._table_row(table, r, lbl, val, col)

    @staticmethod
    def _set_row(table, row: int, cells, *, colors=None, align_from: int = 1) -> None:
        """Populate one table row from ``cells`` (column strings). ``colors`` maps a column index to a
        foreground colour; columns at/after ``align_from`` are right-aligned. The shared body of the
        trades / by-symbol / runs-history fills (each differs only in cells + which columns colour)."""
        for c, text in enumerate(cells):
            item = QtWidgets.QTableWidgetItem(text)
            col = colors.get(c) if colors else None
            if col:
                item.setForeground(QtGui.QColor(col))
            if c >= align_from:
                item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            table.setItem(row, c, item)

    def _fill_robustness(self, report) -> None:
        from ..analysis.overfit import probabilistic_sharpe_ratio

        rows: list[tuple[str, str, str | None]] = []  # (label, value, color)

        # PSR — needs per-observation Sharpe = Sharpe / sqrt(ppy).
        eq = report.equity_curve or []
        n = len(eq)
        psr_val = "—"
        if n >= 3:
            try:
                # Annualise: ppy≈252 daily; compute per-obs Sharpe from the curve returns
                rets = [eq[i] / eq[i - 1] - 1.0 for i in range(1, n) if eq[i - 1] != 0]
                if len(rets) >= 2:
                    import statistics
                    mu = statistics.mean(rets)
                    sd = statistics.stdev(rets)
                    sr_per_obs = mu / sd if sd > 0 else 0.0
                    psr = probabilistic_sharpe_ratio(sr_per_obs, len(rets))
                    psr_val = self._pct(psr)
            except Exception:  # noqa: BLE001
                psr_val = "—"

        sharpe_raw = getattr(report, "sharpe", None)
        sharpe_str = self._fmt("sharpe", sharpe_raw) if sharpe_raw is not None else "—"

        rows.append(("PSR (Probabilistic Sharpe)", psr_val, None))
        rows.append(("Sharpe Ratio", sharpe_str, None))

        verdict = getattr(report, "verdict", None)
        if verdict is not None:
            level = verdict.level
            color = theme.VERDICT.get(level, theme.WARN)
            rows.append(("Overfit Risk Level", level, color))
            reason = verdict.reasons[0] if verdict.reasons else ""
            rows.append(("Primary Flag", reason, None))
            # PBO and Deflated Sharpe are not stored on Verdict directly — they live as
            # formatted strings in the reasons text; surface what we have.
        else:
            rows.append(("Overfit verdict", "Run Optimize/Walk-forward to populate", None))

        self._fill_metric_table(self._robust_table, rows)

    def _fill_montecarlo(self, report) -> None:
        from ..analysis.montecarlo import mc_summary

        trades = getattr(report, "trades", None) or []
        if not trades:
            self._mc_table.setRowCount(1)
            self._table_row(self._mc_table, 0, "Monte Carlo", "No trades — run a backtest first", None)
            return

        pnls = [t.pnl for t in trades]
        net = getattr(report, "net_profit", 0.0) or 0.0
        final = getattr(report, "final_equity", 0.0) or 0.0
        start_equity = final - net
        if start_equity <= 0:
            start_equity = 100_000.0

        try:
            s = mc_summary(pnls, start_equity=start_equity, n_sims=1000, seed=0)
        except Exception:  # noqa: BLE001
            self._mc_table.setRowCount(1)
            self._table_row(self._mc_table, 0, "Monte Carlo", "Failed to compute", None)
            return

        rows: list[tuple[str, str, str | None]] = [
            ("Terminal Equity P5", self._money(s["terminal_p5"]),
             theme.DOWN if s["terminal_p5"] < start_equity else theme.UP),
            ("Terminal Equity P50 (median)", self._money(s["terminal_p50"]),
             theme.DOWN if s["terminal_p50"] < start_equity else theme.UP),
            ("Terminal Equity P95", self._money(s["terminal_p95"]),
             theme.UP if s["terminal_p95"] >= start_equity else theme.DOWN),
            ("Max Drawdown P50", self._pct(s["max_dd_p50"]), theme.DOWN),
            ("Max Drawdown P95", self._pct(s["max_dd_p95"]), theme.DOWN),
            ("Prob(final < start)", self._pct(s["prob_loss"]),
             theme.DOWN if s["prob_loss"] > 0.5 else theme.TEXT),
            ("Risk of Ruin (50% loss)", self._pct(s["risk_of_ruin"]),
             theme.DOWN if s["risk_of_ruin"] > 0.05 else theme.TEXT),
        ]
        self._fill_metric_table(self._mc_table, rows)

    def _fill_periods(self, report) -> None:
        from ..analysis.periods import drawdown_table, monthly_return_matrix
        from datetime import datetime, timezone

        eq = getattr(report, "equity_curve", None) or []
        ts = getattr(report, "equity_ts", None) or []

        if not eq or not ts or len(eq) != len(ts):
            # Single-symbol run or missing timestamps — show a graceful hint row
            self._periods_table.setRowCount(1)
            item = QtWidgets.QTableWidgetItem(
                "Periodic returns require a portfolio run (equity timestamps not available)"
            )
            item.setForeground(QtGui.QColor(theme.TEXT3))
            self._periods_table.setItem(0, 0, item)
            self._dd_table.setRowCount(0)
            return

        # Monthly return matrix
        try:
            mat = monthly_return_matrix(eq, ts)
        except Exception:  # noqa: BLE001
            self._periods_table.setRowCount(0)
            self._dd_table.setRowCount(0)
            return

        years = mat["years"]
        matrix = mat["matrix"]
        annual = mat["annual"]
        self._periods_table.setRowCount(len(years))
        for r, year in enumerate(years):
            year_item = QtWidgets.QTableWidgetItem(str(year))
            year_item.setTextAlignment(QtCore.Qt.AlignCenter | QtCore.Qt.AlignVCenter)
            self._periods_table.setItem(r, 0, year_item)
            months = matrix.get(year, {})
            for m in range(1, 13):
                ret = months.get(m)
                text = self._pct(ret) if ret is not None else "—"
                cell = QtWidgets.QTableWidgetItem(text)
                cell.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                if ret is not None:
                    cell.setForeground(QtGui.QColor(theme.UP if ret >= 0 else theme.DOWN))
                self._periods_table.setItem(r, m, cell)  # col 1..12 = Jan..Dec
            ann = annual.get(year)
            ann_item = QtWidgets.QTableWidgetItem(self._pct(ann) if ann is not None else "—")
            ann_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            if ann is not None:
                ann_item.setForeground(QtGui.QColor(theme.UP if ann >= 0 else theme.DOWN))
            self._periods_table.setItem(r, 13, ann_item)

        # Drawdown table
        try:
            dds = drawdown_table(eq, ts, top_n=5)
        except Exception:  # noqa: BLE001
            dds = []

        self._dd_table.setRowCount(len(dds))
        for r, ep in enumerate(dds):
            depth_item = QtWidgets.QTableWidgetItem(self._pct(ep["depth"]))
            depth_item.setForeground(QtGui.QColor(theme.DOWN))
            depth_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

            def _ts_str(epoch_ms):
                if epoch_ms is None:
                    return "—"
                dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
                return dt.strftime("%Y-%m-%d")

            peak_item = QtWidgets.QTableWidgetItem(_ts_str(ep["peak_ts"]))
            trough_item = QtWidgets.QTableWidgetItem(_ts_str(ep["trough_ts"]))
            length_item = QtWidgets.QTableWidgetItem(str(ep["length"]))
            length_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

            self._dd_table.setItem(r, 0, depth_item)
            self._dd_table.setItem(r, 1, peak_item)
            self._dd_table.setItem(r, 2, trough_item)
            self._dd_table.setItem(r, 3, length_item)

    def _fill_benchmark(self, report) -> None:
        from ..analysis.benchmark import benchmark_stats

        bench_curve = getattr(report, "benchmark_curve", None) or []
        eq_curve = getattr(report, "equity_curve", None) or []
        bench_label = getattr(report, "benchmark_label", "") or "Equal-weight buy & hold"

        if not bench_curve or not eq_curve or len(bench_curve) != len(eq_curve):
            self._bench_table.setRowCount(1)
            self._table_row(self._bench_table, 0, "Benchmark",
                            "Benchmark needs a portfolio run", None)
            return

        try:
            stats = benchmark_stats(eq_curve, bench_curve, periods_per_year=252)
        except Exception:  # noqa: BLE001
            self._bench_table.setRowCount(1)
            self._table_row(self._bench_table, 0, "Benchmark", "Failed to compute", None)
            return

        rows: list[tuple[str, str, str | None]] = [
            ("Benchmark", bench_label, None),
            ("Alpha (annualized)", self._pct(stats["alpha"]),
             theme.UP if stats["alpha"] > 0 else theme.DOWN if stats["alpha"] < 0 else None),
            ("Beta", f"{stats['beta']:.2f}", None),
            ("Correlation", f"{stats['correlation']:.2f}", None),
            ("R²", f"{stats['r_squared']:.2f}", None),
            ("Tracking Error", self._pct(stats["tracking_error"]), None),
            ("Information Ratio", f"{stats['information_ratio']:.2f}",
             theme.UP if stats["information_ratio"] > 0 else theme.DOWN if stats["information_ratio"] < 0 else None),
            ("Up Capture", self._pct(stats["up_capture"]),
             theme.UP if stats["up_capture"] >= 1.0 else None),
            ("Down Capture", self._pct(stats["down_capture"]),
             theme.UP if stats["down_capture"] < 1.0 and stats["down_capture"] != 0 else None),
        ]
        self._fill_metric_table(self._bench_table, rows)

    # --- walk-forward matrix + optimization surface ---

    def show_walk_forward(self, wf, criterion: str = "sharpe") -> None:
        """Populate the WF Matrix tab from a WalkForwardReport: per-window IS vs OOS + PASS/FAIL."""
        windows = list(getattr(wf, "windows", []) or [])
        verdict = getattr(getattr(wf, "oos_report", None), "verdict", None)
        level = verdict.level if verdict is not None else "?"
        eff = getattr(wf, "wf_efficiency", 0.0) or 0.0
        eff_str = f"{eff:.2f}" if eff else "—"  # 0.0 = non-positive IS edge (see wf_efficiency)
        cons = getattr(wf, "wf_consistency", 0.0) or 0.0
        n_pass = sum(1 for w in windows if getattr(w.oos_report, "total_return", 0.0) > 0)
        self._wf_summary.setText(
            f"Criterion: {criterion}  ·  Windows: {len(windows)}  ·  Passed OOS: {n_pass}/{len(windows)}"
            f"  ·  WF efficiency: {eff_str}  ·  Consistency: {self._pct(cons)}  ·  Overfit risk: {level}"
        )
        self._wf_table.setRowCount(len(windows))
        for r, w in enumerate(windows):
            passed = getattr(w.oos_report, "total_return", 0.0) > 0
            params = ", ".join(f"{k}={_fmt_param(v)}" for k, v in (w.best_params or {}).items())
            cells = [
                str(r + 1), _range_str(w.train_range), _range_str(w.test_range), params,
                self._fmt(criterion, getattr(w, "is_score", 0.0)),
                self._fmt(criterion, getattr(w, "oos_score", 0.0)),
                "PASS" if passed else "FAIL",
            ]
            for c, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if c in (4, 5):
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                if c == 5:
                    item.setForeground(QtGui.QColor(theme.UP if passed else theme.DOWN))
                if c == 6:
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
                    item.setForeground(QtGui.QColor(theme.BG))
                    item.setBackground(QtGui.QColor(theme.UP if passed else theme.DOWN))
                self._wf_table.setItem(r, c, item)

    def show_surface(self, ranked, grid, criterion: str = "sharpe") -> None:
        """Populate the Surface tab from optimization trials (each with .params/.score) over a grid."""
        self._surface_trials = list(ranked or [])
        self._surface_grid = dict(grid or {})
        self._surface_criterion = criterion
        multi = [k for k, vs in self._surface_grid.items() if len(vs) >= 2]
        axes = multi or list(self._surface_grid)
        if len(axes) < 2 or not self._surface_trials:
            self._surface_caption.setText("Optimization surface needs a ≥2-parameter grid search.")
            for combo in (self._surface_x, self._surface_y):
                combo.blockSignals(True)
                combo.clear()
                combo.blockSignals(False)
            return
        for combo, default in ((self._surface_x, axes[0]), (self._surface_y, axes[1])):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(axes)
            combo.setCurrentText(default)
            combo.blockSignals(False)
        tail = "" if _gl_usable() else "  (2D — install vike_trader_app[viz3d] for 3D)"
        self._surface_caption.setText(f"Optimization surface · {criterion}{tail}")
        self._render_surface()

    def _render_surface(self, *_args) -> None:
        if not self._surface_trials or self._surface_x.count() == 0:
            return
        px, py = self._surface_x.currentText(), self._surface_y.currentText()
        if not px or not py:
            return
        if px == py:  # keep the two axes distinct
            for i in range(self._surface_y.count()):
                if self._surface_y.itemText(i) != px:
                    self._surface_y.blockSignals(True)
                    self._surface_y.setCurrentIndex(i)
                    self._surface_y.blockSignals(False)
                    py = self._surface_y.currentText()
                    break
            if px == py:
                return
        from ..analysis.surface import surface_from_trials
        best = max(self._surface_trials, key=lambda t: getattr(t, "score", float("-inf")))
        best_params = getattr(best, "params", {}) or {}
        fixed = {k: best_params[k] for k in self._surface_grid if k not in (px, py) and k in best_params}
        self._draw_surface(surface_from_trials(self._surface_trials, px, py, fixed=fixed))

    def _draw_surface(self, surf) -> None:
        flat = [v for row in surf.z for v in row if v is not None]
        if not flat:  # no trial matches these axes — blank rather than leave a stale image
            self._surface_img.clear()
            self._surface_stack.setCurrentWidget(self._surface_img)
            return
        floor = min(flat)
        z = np.array([[(v if v is not None else floor) for v in row] for row in surf.z], dtype=float)
        if _gl_usable():
            try:
                self._draw_surface_gl(z)
                return
            except Exception:  # noqa: BLE001 - fall back to the 2D heatmap on any GL hiccup
                pass
        self._surface_img.setImage(z.T, autoLevels=True)  # ImageView wants [x][y]
        self._surface_stack.setCurrentWidget(self._surface_img)

    def _draw_surface_gl(self, z) -> None:
        global _gl
        if _gl is None:
            import pyqtgraph.opengl as _gl   # deferred ~280ms import; only on the first real 3D draw
        if self._surface_gl is None:
            self._surface_gl = _gl.GLViewWidget()
            self._surface_gl.setBackgroundColor(theme.BG)
            self._surface_gl_item = _gl.GLSurfacePlotItem(shader="heightColor", smooth=True,
                                                          computeNormals=False)
            self._surface_gl.addItem(self._surface_gl_item)
            self._surface_gl.addItem(_gl.GLGridItem())
            self._surface_stack.addWidget(self._surface_gl)
        ny, nx = z.shape
        zmin, zmax = float(z.min()), float(z.max())
        span = (zmax - zmin) or 1.0
        znorm = (z - zmin) / span * max(nx, ny) * 0.5  # visible relief
        self._surface_gl_item.setData(x=np.arange(nx, dtype=float), y=np.arange(ny, dtype=float),
                                      z=znorm.T)  # GLSurfacePlotItem z is z[x][y]
        self._surface_gl.setCameraPosition(distance=max(nx, ny) * 2.2)
        self._surface_stack.setCurrentWidget(self._surface_gl)

    def _on_run_clicked(self, row: int, _col: int) -> None:
        """Run-row click -> re-display that stored run (iterate-and-compare loop)."""
        if 0 <= row < len(self._runs):
            r = self._runs[row]
            self.show_report(r["report"], r["bars"], r["overlays"])

    # --- public ---

    def show_report(self, report, bars=None, overlays=None) -> None:
        """Display a TesterReport: hero KPIs + detail grid + equity curve + trades."""
        self.last_report = report
        self._status.setVisible(False)
        self._status.setText("")

        d = self._derive(report, bars)

        # hero tiles
        self._hero_val["roi"].setText(self._pct(report.total_return))
        self._hero_sub["roi"].setText(self._money(d["roi_dollars"]))
        self._hero_val["annualized"].setText(self._pct(d["annualized"]))
        self._hero_sub["annualized"].setText("")
        self._hero_val["win_ratio"].setText(self._pct(report.win_rate))
        self._hero_sub["win_ratio"].setText(f"{d['wins']} of {d['n']}")
        self._hero_val["max_drawdown"].setText(self._pct(report.max_drawdown))
        self._hero_sub["max_drawdown"].setText(
            self._money(-d["dd_dollars"]) if d["dd_dollars"] is not None else ""
        )
        self._hero_val["time_in_market"].setText(self._pct(d["time_in_market"]))
        self._hero_sub["time_in_market"].setText("")
        pf = report.profit_factor
        self._hero_val["profit_factor"].setText(self._fmt("profit_factor", pf))
        self._hero_sub["profit_factor"].setText("")
        for key, val in (
            ("roi", report.total_return), ("annualized", d["annualized"]),
            ("win_ratio", report.win_rate), ("max_drawdown", report.max_drawdown),
            ("profit_factor", pf),
        ):
            self._hero_val[key].setStyleSheet(self._hero_color(key, val))

        # detail grid
        for key, _ in self._DETAIL:
            raw = getattr(report, key, None)
            self._value_labels[key].setText(self._fmt(key, raw))
            self._value_labels[key].setStyleSheet(self._metric_style(key, raw))

        self._set_banner(report.verdict)

        if report.equity_curve:
            try:
                self._equity.set_data(report.equity_curve)
            except Exception:  # noqa: BLE001
                pass

        mm = report_extras.mfe_mae(report.trades, bars) if bars else None
        self._fill_trades(report.trades, mm)
        self._fill_by_symbol(report)
        self._update_distribution(report_extras.trade_returns(report.trades))
        self._fill_robustness(report)
        self._fill_montecarlo(report)
        self._fill_periods(report)
        self._fill_benchmark(report)
        self._tabs.setCurrentIndex(0)  # land on the Equity tab — the headline view

    def add_run(self, report, bars=None, overlays=None) -> None:
        """Record a run in the history table (versioned) and display it."""
        self._runs.append({"report": report, "bars": bars, "overlays": overlays})
        n = len(self._runs)
        row = n - 1
        self._runs_table.insertRow(row)
        cells = [
            str(n),
            self._pct(report.total_return),
            self._pct(report.max_drawdown),
            str(report.n_trades),
            self._fmt("sharpe", report.sharpe),
        ]
        colors = {
            1: theme.UP if report.total_return >= 0 else theme.DOWN,
            2: theme.DOWN if report.max_drawdown > 0 else theme.TEXT,
        }
        self._set_row(self._runs_table, row, cells, colors=colors)
        self.show_report(report, bars, overlays)
        self.toast(f"✓ Backtest complete · run {n}")

    def _position_status(self) -> None:
        """Park the status overlay at the top-right of the tab-strip row."""
        self._status.adjustSize()
        x = max(self.width() - self._status.width() - _GAP, 0)
        self._status.move(x, _GAP)
        self._status.raise_()

    def resizeEvent(self, e):  # noqa: N802 - keep the status overlay pinned top-right
        super().resizeEvent(e)
        self._position_status()

    def toast(self, msg: str) -> None:
        """Transient success line (green)."""
        self._status.setText(msg)
        self._status.setStyleSheet(f"color:{theme.UP};font-size:11px;padding:4px 8px;")
        self._status.setVisible(True)
        self._position_status()

    def _reset_metric_labels(self) -> None:
        """Blank the Performance hero tiles + detail metric labels (shared by clear / show_error)."""
        for lbl in self._value_labels.values():
            lbl.setText("—")
            lbl.setStyleSheet(self._metric_style("", None))
        for lbl in self._hero_val.values():
            lbl.setText("—")
        for lbl in self._hero_sub.values():
            lbl.setText("")

    def _clear_report_tables(self) -> None:
        """Empty every per-report results table (shared by clear / show_error). The Runs-history
        table is intentionally NOT here — only clear() wipes run history."""
        for t in (self._trades, self._by_symbol_table, self._robust_table, self._mc_table,
                  self._periods_table, self._dd_table, self._bench_table):
            t.setRowCount(0)

    def show_error(self, msg: str) -> None:
        """Display an error message; clear any previous report."""
        self.last_report = None
        self._report_trades = []
        self._banner.setVisible(False)
        self._status.setText(msg)
        self._status.setStyleSheet(f"color:{theme.DOWN};font-size:11px;padding:4px 8px;")
        self._status.setVisible(True)
        self._position_status()
        self._reset_metric_labels()
        self._clear_report_tables()
        self._reset_wf_surface()

    def _reset_wf_surface(self) -> None:
        """Blank the WF matrix + optimization surface (used by clear / show_error)."""
        self._wf_table.setRowCount(0)
        self._wf_summary.setText("Run Walk-forward to populate the matrix.")
        self._surface_trials = []
        self._surface_caption.setText("Run Walk-forward on a ≥2-param grid for the surface.")

    def clear(self) -> None:
        """Reset to blank state (including run history)."""
        self.last_report = None
        self._report_trades = []
        self._runs = []
        self._banner.setVisible(False)
        self._status.setVisible(False)
        self._reset_metric_labels()
        self._clear_report_tables()
        self._runs_table.setRowCount(0)   # run-history table — clear() only
        self._reset_wf_surface()


# ---------------------------------------------------------------------------
# ChatPanel
# ---------------------------------------------------------------------------

_EXAMPLES = [
    "SMA crossover on BTC with a 2×ATR trailing stop",
    "RSI mean-reversion — buy below 30, exit above 55",
    "Donchian breakout with volatility-scaled sizing",
]


class _ExampleCard(QtWidgets.QFrame):
    """A clickable example-prompt card (fills the prompt input when clicked)."""

    clicked = QtCore.Signal(str)

    def __init__(self, text: str, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._text = text
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setStyleSheet(
            f"_ExampleCard{{background:{theme.PANEL2};border:1px solid {theme.BORDER};"
            f"border-radius:10px;}}"
            f"_ExampleCard:hover{{border-color:{theme.ACCENT};}}"
        )
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_PAD, _PAD, _PAD, _PAD)
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{theme.TEXT2};border:none;background:transparent;")
        lay.addWidget(label)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self._text)
        super().mouseReleaseEvent(event)


class ChatPanel(QtWidgets.QWidget):
    """Empty-state (heading + example cards) → chat log, plus prompt input + AI button."""

    promptSubmitted = QtCore.Signal(str)
    providerChanged = QtCore.Signal(str)
    cerebrasKeyChanged = QtCore.Signal(str)
    connectRequested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        # Zero inset — StudioTab's root margin + the splitter handles are the only gaps, so every
        # content-to-content distance equals _GAP (a card inset here would double up with the handle).
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(_GAP)

        # --- AI provider toggle + "Connect to Claude" (subscription path) ---
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(_GAP)
        self._provider = SegmentedControl(["Claude", "Cerebras"])
        self._provider.valueChanged.connect(self._on_provider)
        header.addWidget(self._provider)
        header.addStretch(1)
        self._btn_connect = QtWidgets.QPushButton("🤖 Connect")
        self._btn_connect.setToolTip(
            "Wire your local Claude (Desktop / Code) to this app over MCP — drive backtests on "
            "your own Claude Pro/Max subscription."
        )
        self._btn_connect.clicked.connect(self.connectRequested.emit)
        header.addWidget(self._btn_connect)
        root.addLayout(header)

        # --- Cerebras API-key field (hidden unless Cerebras is selected) ---
        self._key_row = QtWidgets.QWidget()
        kr = QtWidgets.QHBoxLayout(self._key_row)
        kr.setContentsMargins(0, 0, 0, 0)
        kr.setSpacing(_GAP)
        klabel = QtWidgets.QLabel("Cerebras key")
        klabel.setStyleSheet(f"color:{theme.TEXT2};font-size:12px;")
        kr.addWidget(klabel)
        self._key_input = QtWidgets.QLineEdit()
        self._key_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self._key_input.setPlaceholderText("csk-… (stored locally on this machine)")
        self._key_input.editingFinished.connect(
            lambda: self.cerebrasKeyChanged.emit(self._key_input.text().strip())
        )
        kr.addWidget(self._key_input, 1)
        self._key_row.hide()
        root.addWidget(self._key_row)

        # --- empty state (shown until the first message) ---
        self._empty = self._build_empty()
        root.addWidget(self._empty, stretch=1)

        # --- chat log (hidden until the first message) ---
        self._log = QtWidgets.QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            f"QTextEdit{{background:{theme.PANEL};border:1px solid {theme.BORDER};"
            f"border-radius:10px;padding:8px;color:{theme.TEXT2};}}"
        )
        self._log.hide()
        root.addWidget(self._log, stretch=1)

        # input row
        input_row = QtWidgets.QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(_GAP)
        self._prompt_input = QtWidgets.QLineEdit()
        self._prompt_input.setPlaceholderText("Describe a strategy …")
        self._prompt_input.returnPressed.connect(self._submit)
        input_row.addWidget(self._prompt_input, stretch=1)

        self._btn_ask = QtWidgets.QPushButton("✦ Ask AI")
        self._btn_ask.setObjectName("play")
        self._btn_ask.clicked.connect(self._submit)
        input_row.addWidget(self._btn_ask)
        root.addLayout(input_row)

    def _build_empty(self) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(box)
        # No own inset — the ChatPanel root's _GAP margin already provides the card padding, so the
        # "✦ AI STUDIO" heading sits flush at the _GAP inset, level with the editor's Run toolbar.
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(_GAP)

        eyebrow = QtWidgets.QLabel("✦ AI STUDIO")
        eyebrow.setStyleSheet(f"color:{theme.ACCENT};font-size:10px;font-weight:700;letter-spacing:2px;")
        heading = QtWidgets.QLabel("Let's build a strategy")
        heading.setStyleSheet(f"color:{theme.TEXT};font-size:18px;font-weight:700;")
        subtitle = QtWidgets.QLabel("Describe an idea in plain English, or start from an example below.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color:{theme.TEXT2};font-size:12px;")
        v.addWidget(eyebrow)
        v.addWidget(heading)
        v.addWidget(subtitle)
        v.addSpacing(_GAP)

        for ex in _EXAMPLES:
            card = _ExampleCard(ex)
            card.clicked.connect(self._prompt_from_card)
            v.addWidget(card)
        v.addStretch(1)
        return box

    def _prompt_from_card(self, text: str) -> None:
        self._prompt_input.setText(text)
        self._prompt_input.setFocus()

    def append_message(self, role: str, text: str) -> None:
        """Append a message to the log with a role prefix (revealing the log on first use)."""
        if self._empty.isVisible():
            self._empty.hide()
            self._log.show()
        color = {
            "system": theme.TEXT3,
            "user": theme.TEXT,
            "assistant": theme.BLUE,
        }.get(role, theme.TEXT2)
        self._log.append(
            f'<span style="color:{color};font-weight:700">{role.upper()}:</span> '
            f'<span style="color:{theme.TEXT2}">{html.escape(text)}</span>'
        )

    def _submit(self) -> None:
        prompt = self._prompt_input.text().strip()
        if prompt:
            self.promptSubmitted.emit(prompt)
            self._prompt_input.clear()

    def set_busy(self, busy: bool) -> None:
        """Disable the prompt + Ask button while an AI request is in flight."""
        self._btn_ask.setEnabled(not busy)
        self._prompt_input.setEnabled(not busy)

    # --- AI provider selection ---

    def _on_provider(self, value: str) -> None:
        self._key_row.setVisible(value.lower() == "cerebras")
        self.providerChanged.emit(value.lower())

    def provider(self) -> str:
        return (self._provider.value() or "claude").lower()

    def set_provider(self, name: str) -> None:
        label = "Cerebras" if str(name).lower() == "cerebras" else "Claude"
        self._provider.setValue(label)
        self._key_row.setVisible(label == "Cerebras")

    def cerebras_key(self) -> str:
        return self._key_input.text().strip()

    def set_cerebras_key(self, key: str) -> None:
        self._key_input.setText(key or "")


# ---------------------------------------------------------------------------
# ChatWorker
# ---------------------------------------------------------------------------

class ChatWorker(QtCore.QThread):
    """Background thread: calls develop_strategy and emits the AgentResult."""

    result = QtCore.Signal(object)

    def __init__(self, client, prompt: str, bars: list, config):
        super().__init__()
        self._client = client
        self._prompt = prompt
        self._bars = bars
        self._config = config

    def run(self) -> None:
        try:
            from vike_trader_app.ai.agent import develop_strategy
            res = develop_strategy(
                self._prompt, self._bars, client=self._client, config=self._config
            )
        except Exception as exc:  # noqa: BLE001
            from vike_trader_app.ai.agent import AgentResult
            res = AgentResult(
                code="", explanation=str(exc), accepted=False,
                attempts=0, problems=[str(exc)],
            )
        self.result.emit(res)


# ---------------------------------------------------------------------------
# SegmentedControl
# ---------------------------------------------------------------------------

class SegmentedControl(QtWidgets.QWidget):
    """A pill group of mutually-exclusive options (TradeLocker's resolution selector).

    Exclusive checkable buttons in a rounded track; emits ``valueChanged(str)``. Options
    can be disabled (e.g. resolutions finer than the loaded base data, which we can't
    synthesize). Use ``value()`` / ``setValue()`` to read/set the active option.
    """

    valueChanged = QtCore.Signal(str)

    def __init__(self, options, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"SegmentedControl{{background:{theme.PANEL2};border:1px solid {theme.BORDER};"
            f"border-radius:9px;}}"
        )
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QtWidgets.QPushButton] = {}
        for opt in options:
            btn = QtWidgets.QPushButton(opt)
            btn.setCheckable(True)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton{{background:transparent;border:none;border-radius:7px;"
                f"padding:6px 14px;color:{theme.TEXT3};font-weight:600;}}"
                f"QPushButton:hover{{color:{theme.TEXT2};}}"
                f"QPushButton:checked{{background:{theme.RAISE};color:{theme.TEXT};}}"
                f"QPushButton:disabled{{color:{theme.TEXT3};}}"
            )
            btn.clicked.connect(lambda _c, o=opt: self.valueChanged.emit(o))
            self._group.addButton(btn)
            row.addWidget(btn)
            self._buttons[opt] = btn

    def set_enabled_options(self, enabled) -> None:
        """Enable only the options in ``enabled`` (an iterable of labels); disable the rest."""
        allow = set(enabled)
        for opt, btn in self._buttons.items():
            btn.setEnabled(opt in allow)

    def value(self) -> str | None:
        for opt, btn in self._buttons.items():
            if btn.isChecked():
                return opt
        return None

    def setValue(self, opt: str) -> None:  # noqa: N802 - Qt-style setter
        btn = self._buttons.get(opt)
        if btn is not None and btn.isEnabled():
            btn.setChecked(True)


# ---------------------------------------------------------------------------
# BacktestConfigDialog
# ---------------------------------------------------------------------------

# resolution label -> window in epoch ms (parse_timeframe wants lowercase units, so we
# keep an explicit display-label map that includes the upper-case 1H/4H/1D forms).
_RESOLUTIONS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000,
}


def _base_resolution_ms(bars) -> int | None:
    """Infer the loaded data's base bar spacing (min positive gap across a sample)."""
    if not bars or len(bars) < 2:
        return None
    gaps = [bars[i + 1].ts - bars[i].ts for i in range(min(len(bars) - 1, 200))]
    pos = [g for g in gaps if g > 0]
    return min(pos) if pos else None


class BacktestConfigDialog(QtWidgets.QDialog):
    """Per-run config — capital + resolution + date range over the loaded bars.

    Mirrors TradeLocker's "Backtest" modal: a segmented resolution selector (resolutions
    finer than the loaded base data are disabled — we synthesize coarser bars by OHLCV
    aggregation but never invent finer ones), plus starting capital and a date sub-range.
    """

    def __init__(self, bars, capital: float = 10_000.0, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Backtest configuration")
        self.setModal(True)
        self.setMinimumWidth(440)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        eyebrow = QtWidgets.QLabel("BACKTEST")
        eyebrow.setStyleSheet(f"color:{theme.ACCENT};font-size:10px;font-weight:700;letter-spacing:2px;")
        title = QtWidgets.QLabel("Run configuration")
        title.setStyleSheet(f"color:{theme.TEXT};font-size:18px;font-weight:700;")
        root.addWidget(eyebrow)
        root.addWidget(title)

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        # resolution segmented control (disable options finer than the base data)
        self._base_ms = _base_resolution_ms(bars)
        self.resolution = SegmentedControl(list(_RESOLUTIONS.keys()))
        if self._base_ms is not None:
            enabled = [lbl for lbl, ms in _RESOLUTIONS.items() if ms >= self._base_ms]
            self.resolution.set_enabled_options(enabled or list(_RESOLUTIONS.keys()))
            # default to the base resolution if it lines up, else the finest enabled
            base_lbl = next((lbl for lbl, ms in _RESOLUTIONS.items() if ms == self._base_ms), None)
            self.resolution.setValue(base_lbl or (enabled[0] if enabled else "1m"))
        else:
            self.resolution.setValue("1m")
        form.addRow("Resolution", self.resolution)

        self.capital = QtWidgets.QDoubleSpinBox()
        self.capital.setRange(1.0, 1e9)
        self.capital.setDecimals(2)
        self.capital.setPrefix("$ ")
        self.capital.setGroupSeparatorShown(True)
        self.capital.setValue(float(capital))
        form.addRow("Starting capital", self.capital)

        self.start = QtWidgets.QDateEdit()
        self.end = QtWidgets.QDateEdit()
        for w in (self.start, self.end):
            w.setCalendarPopup(True)
        if bars:
            d0 = QtCore.QDateTime.fromMSecsSinceEpoch(int(bars[0].ts)).date()
            d1 = QtCore.QDateTime.fromMSecsSinceEpoch(int(bars[-1].ts)).date()
            for w in (self.start, self.end):
                w.setDateRange(d0, d1)
            self.start.setDate(d0)
            self.end.setDate(d1)
        form.addRow("Start date", self.start)
        form.addRow("End date", self.end)
        root.addLayout(form)

        if bars:
            note = QtWidgets.QLabel(f"{len(bars):,} base bars loaded")
            note.setStyleSheet(f"color:{theme.TEXT3};font-size:11px;")
            root.addWidget(note)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        run = btns.addButton("Run", QtWidgets.QDialogButtonBox.AcceptRole)
        run.setObjectName("play")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def values(self) -> BacktestConfig:
        """BacktestConfig with inclusive dates (epoch ms). ``resolution_ms`` is None when the chosen
        resolution equals/is-finer-than the base data (no resampling); else the coarse aggregate window."""
        res_ms = _RESOLUTIONS.get(self.resolution.value())
        if res_ms is not None and self._base_ms is not None and res_ms <= self._base_ms:
            res_ms = None  # same as (or finer than) base -> no resampling
        return BacktestConfig(
            capital=self.capital.value(),
            start_ts=QtCore.QDateTime(self.start.date(), QtCore.QTime(0, 0, 0)).toMSecsSinceEpoch(),
            end_ts=QtCore.QDateTime(self.end.date(), QtCore.QTime(23, 59, 59)).toMSecsSinceEpoch(),
            resolution_ms=res_ms,
        )


class OptimizerConfigDialog(QtWidgets.QDialog):
    """Optimizer + walk-forward configuration: search method, ranking criterion, WF window mode.

    The MultiCharts "Optimization" + "Walk-Forward" settings in one modal: pick exhaustive grid vs
    random / genetic / Bayesian (Optuna TPE/GP/CMA-ES) search, the metric to rank by, and whether
    the walk-forward train window is anchored (expanding) or rolling (fixed-width sliding).
    """

    def __init__(self, config: OptimizerConfig, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Optimizer configuration")
        self.setModal(True)
        self.setMinimumWidth(440)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        eyebrow = QtWidgets.QLabel("OPTIMIZER")
        eyebrow.setStyleSheet(f"color:{theme.ACCENT};font-size:10px;font-weight:700;letter-spacing:2px;")
        title = QtWidgets.QLabel("Search & walk-forward")
        title.setStyleSheet(f"color:{theme.TEXT};font-size:18px;font-weight:700;")
        root.addWidget(eyebrow)
        root.addWidget(title)

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)

        self.method = QtWidgets.QComboBox()
        methods = _OPT_METHODS if _HAS_OPTUNA else [m for m in _OPT_METHODS if m != "bayesian"]
        self.method.addItems(methods)
        self.method.setCurrentText(config.method if config.method in methods else "grid")
        self.criterion = QtWidgets.QComboBox()
        self.criterion.addItems(_OPT_CRITERIA)
        self.criterion.setCurrentText(config.criterion)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(_WF_MODES)
        self.mode.setCurrentText(config.mode)
        self.sampler = QtWidgets.QComboBox()
        self.sampler.addItems(_BAYES_SAMPLERS)
        self.sampler.setCurrentText(config.sampler)

        def _spin(lo, hi, val):
            s = QtWidgets.QSpinBox()
            s.setRange(lo, hi)
            s.setValue(int(val))
            return s

        self.n_splits = _spin(2, 20, config.n_splits)
        self.n_trials = _spin(5, 2000, config.n_trials)
        self.pop_size = _spin(4, 500, config.pop_size)
        self.generations = _spin(1, 500, config.generations)
        self.seed = _spin(0, 1_000_000, config.seed)

        form.addRow("Search method", self.method)
        form.addRow("Ranking criterion", self.criterion)
        form.addRow("Walk-forward mode", self.mode)
        form.addRow("WF splits", self.n_splits)
        form.addRow("Trials (random / bayesian)", self.n_trials)
        form.addRow("Population (genetic)", self.pop_size)
        form.addRow("Generations (genetic)", self.generations)
        form.addRow("Bayesian sampler", self.sampler)
        form.addRow("Seed", self.seed)
        root.addLayout(form)

        hint_text = ("Grid is exhaustive; genetic/bayesian scale to large grids. "
                     "Rolling WF uses a fixed-width sliding train window.")
        if not _HAS_OPTUNA:
            hint_text += "  Bayesian needs the optional vike_trader_app[opt] extra (optuna)."
        hint = QtWidgets.QLabel(hint_text)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{theme.TEXT3};font-size:11px;")
        root.addWidget(hint)

        self.method.currentTextChanged.connect(self._sync_enabled)
        self._sync_enabled()

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        btns.addButton("Save", QtWidgets.QDialogButtonBox.AcceptRole)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _sync_enabled(self) -> None:
        m = self.method.currentText()
        self.n_trials.setEnabled(m in ("random", "bayesian"))
        self.pop_size.setEnabled(m == "genetic")
        self.generations.setEnabled(m == "genetic")
        self.sampler.setEnabled(m == "bayesian")

    def values(self) -> OptimizerConfig:
        return OptimizerConfig(
            method=self.method.currentText(),
            criterion=self.criterion.currentText(),
            mode=self.mode.currentText(),
            n_splits=self.n_splits.value(),
            n_trials=self.n_trials.value(),
            pop_size=self.pop_size.value(),
            generations=self.generations.value(),
            sampler=self.sampler.currentText(),
            seed=self.seed.value(),
        )


# ---------------------------------------------------------------------------
# DiffDialog
# ---------------------------------------------------------------------------

class DiffDialog(QtWidgets.QDialog):
    """Side-by-side 'current vs AI-proposed' code diff with Apply / Reject.

    Human-in-the-loop gate (TradeLocker's AI never silently edits): removed lines shade
    red on the left, added lines shade green on the right. Applying bumps the version.
    """

    def __init__(self, current: str, proposed: str, version: int,
                 parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"AI proposed change → v{version}")
        self.setModal(True)
        self.resize(940, 580)
        root = QtWidgets.QVBoxLayout(self)
        left_html, right_html = self._diff_html(current, proposed)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self._pane(f"Current · v{version - 1} (read only)", left_html), 1)
        row.addWidget(self._pane("Proposed", right_html), 1)
        root.addLayout(row, 1)

        btns = QtWidgets.QDialogButtonBox()
        apply = btns.addButton("Apply", QtWidgets.QDialogButtonBox.AcceptRole)
        apply.setObjectName("play")
        btns.addButton("Reject", QtWidgets.QDialogButtonBox.RejectRole)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    @staticmethod
    def _pane(title: str, body_html: str) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        cap = QtWidgets.QLabel(title.upper())
        cap.setStyleSheet(f"color:{theme.TEXT3};font-size:9px;letter-spacing:1px;")
        view = QtWidgets.QTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        view.setStyleSheet(
            f"QTextEdit{{background:{theme.PANEL};border:1px solid {theme.BORDER};border-radius:6px;}}"
        )
        view.setHtml(body_html)
        v.addWidget(cap)
        v.addWidget(view, 1)
        return box

    @staticmethod
    def _diff_html(current: str, proposed: str) -> tuple[str, str]:
        cur = current.splitlines() or [""]
        new = proposed.splitlines() or [""]
        sm = difflib.SequenceMatcher(None, cur, new)
        del_bg, add_bg = "rgba(248,81,73,0.18)", "rgba(63,185,80,0.18)"

        def row(s: str, bg: str | None = None) -> str:
            style = f"background:{bg};" if bg else ""
            return (f'<div style="{style}white-space:pre;font-family:monospace;'
                    f'color:{theme.TEXT2}">{html.escape(s) or "&nbsp;"}</div>')

        left, right = [], []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                left += [row(s) for s in cur[i1:i2]]
                right += [row(s) for s in new[j1:j2]]
            elif tag == "replace":
                left += [row(s, del_bg) for s in cur[i1:i2]]
                right += [row(s, add_bg) for s in new[j1:j2]]
            elif tag == "delete":
                left += [row(s, del_bg) for s in cur[i1:i2]]
            elif tag == "insert":
                right += [row(s, add_bg) for s in new[j1:j2]]
        return "".join(left), "".join(right)


# ---------------------------------------------------------------------------
# StudioTab
# ---------------------------------------------------------------------------

class StudioTab(QtWidgets.QWidget):
    """Three-pane studio: ChatPanel | CodeEditor | ResultsPanel with a Run toolbar."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)

        self._bars: list = []
        self._config = None          # set to TesterConfig() on first use
        self._agent_client = None
        self._worker: ChatWorker | None = None  # keep a reference so GC doesn't collect it
        self._run_capital = None     # None -> use config.cash
        self._run_range = None       # None -> full bars, else (start_ts, end_ts)
        self._run_resolution = None  # None -> base bars, else coarse window ms to resample to
        self._apply_version = 0      # AI-applied-change version (for the diff-and-apply flow)
        self._top_collapsed = False  # chart/report panel collapsed to give the cards full height
        self._saved_top_size = None  # remembered top height to restore on expand
        self._ratio_applied = False  # one-time 44:56 re-assert guard (see showEvent)
        self._portfolio_bars = None  # None -> single-symbol mode; dict -> portfolio-optimize mode
        self._portfolio_ranges = None  # per-symbol membership ranges (survivorship-free)
        self._portfolio_name = ""    # DataSet name surfaced in toasts
        # Optimizer + walk-forward config (set via the Optimizer modal; consumed by _optimize).
        self._opt_config = OptimizerConfig()

        root = QtWidgets.QVBoxLayout(self)
        # No root inset — every visible gap is a single, uniform _GAP instead of doubling up
        # The outer frame is ONE _GAP root margin; every inter-pane gap is ONE _GAP splitter
        # handle; the panes themselves have ZERO inset (see ResultsPanel/ChatPanel/editor_pane).
        # So every content-to-content distance — window→content, results↔cards, chat↔editor — is
        # exactly _GAP. (Previously card insets doubled up with the handles: 6 / 12 / 18.)
        root.setContentsMargins(_GAP, _GAP, _GAP, _GAP)
        root.setSpacing(0)

        # toolbar — TradeLocker-style: sits directly ABOVE the code editor (its pane header),
        # not at the top of the whole Studio tab. A wrapping FlowLayout so the buttons never
        # force the editor pane (and the window) wider than the screen.
        toolbar = FlowLayout(margin=0, h_spacing=_GAP, v_spacing=_GAP)
        self._toolbar = toolbar
        self._btn_run = QtWidgets.QPushButton("Run")
        self._btn_run.setObjectName("play")
        self._btn_run.clicked.connect(self.run_code)
        toolbar.addWidget(self._btn_run)
        self._btn_optimize = QtWidgets.QPushButton("Walk-forward")
        self._btn_optimize.setIcon(icons.glyph_icon("scale", theme.TEXT2))
        self._btn_optimize.setIconSize(QtCore.QSize(18, 18))
        self._btn_optimize.setToolTip("Walk-forward optimize the PARAM_GRID + attach an overfit verdict")
        self._btn_optimize.clicked.connect(self._optimize)
        toolbar.addWidget(self._btn_optimize)
        self._btn_opt_config = QtWidgets.QPushButton("Optimizer")
        self._btn_opt_config.setIcon(icons.glyph_icon("gear", theme.TEXT2))
        self._btn_opt_config.setIconSize(QtCore.QSize(16, 16))
        self._btn_opt_config.setToolTip("Optimizer settings — search method, criterion, walk-forward mode")
        self._btn_opt_config.clicked.connect(self._open_optimizer_config)
        toolbar.addWidget(self._btn_opt_config)
        self._btn_templates = QtWidgets.QPushButton()
        self._btn_templates.setIcon(icons.glyph_icon("folder", theme.TEXT2))
        self._btn_templates.setIconSize(QtCore.QSize(18, 18))
        self._btn_templates.setToolTip("Templates")
        self._btn_templates.clicked.connect(self._open_templates)
        toolbar.addWidget(self._btn_templates)
        self._btn_config = QtWidgets.QPushButton()
        self._btn_config.setIcon(icons.glyph_icon("gear", theme.TEXT2))
        self._btn_config.setIconSize(QtCore.QSize(18, 18))
        self._btn_config.setToolTip("Settings")
        self._btn_config.clicked.connect(self._open_config)
        toolbar.addWidget(self._btn_config)
        # Indicators moved to the chart's own toolbar (the ƒx button) — not duplicated here.
        self._btn_export = QtWidgets.QPushButton()
        self._btn_export.setIcon(icons.glyph_icon("save", theme.TEXT2))
        self._btn_export.setIconSize(QtCore.QSize(18, 18))
        self._btn_export.setToolTip("Export CSV")
        self._btn_export.clicked.connect(self._export_csv)
        toolbar.addWidget(self._btn_export)
        # Collapse / expand the top chart+report panel to hand its height to the editor + AI cards.
        # Wired now; _toggle_top_panel reads self._vsplit at click-time (created further below).
        # NOT added to the FlowLayout — it's pushed to the FAR RIGHT of the toolbar row below
        # (a wrapping FlowLayout can't right-align, so a QHBoxLayout + stretch separates it from
        # the Run/Walk-forward/Templates/Settings/Export cluster).
        self._btn_collapse_top = QtWidgets.QToolButton()
        self._btn_collapse_top.setIcon(icons.glyph_icon("chevron_up", theme.TEXT2))
        self._btn_collapse_top.setIconSize(QtCore.QSize(icons.ARROW_PX, icons.ARROW_PX))  # unified arrow size
        self._btn_collapse_top.setCursor(QtCore.Qt.PointingHandCursor)
        # Borderless: a bare chevron like the dropdown carets (no boxy default-QToolButton frame),
        # just a subtle hover. Same glyph/size/weight as every other arrow.
        self._btn_collapse_top.setStyleSheet(
            f"QToolButton{{background:transparent;border:none;padding:3px;"
            f"border-radius:{theme.RADIUS_SM}px;}}"
            f"QToolButton:hover{{background:{theme.HOVER};}}")
        self._btn_collapse_top.setToolTip("Collapse / expand chart & report")
        self._btn_collapse_top.clicked.connect(self._toggle_top_panel)

        self.chat = ChatPanel()
        self.editor = CodeEditor()
        self.results = ResultsPanel()

        # editor pane = toolbar header + code editor (so the buttons sit above the editor)
        editor_pane = QtWidgets.QWidget()
        ep = QtWidgets.QVBoxLayout(editor_pane)
        # Zero inset (see ChatPanel) — the splitter handle + StudioTab root margin are the only gaps.
        ep.setContentsMargins(0, 0, 0, 0)
        ep.setSpacing(_GAP)
        toolbar_flow = QtWidgets.QWidget()
        toolbar_flow.setLayout(toolbar)
        _sp = toolbar_flow.sizePolicy()
        _sp.setHeightForWidth(True)
        _sp.setVerticalPolicy(QtWidgets.QSizePolicy.Minimum)
        toolbar_flow.setSizePolicy(_sp)
        # Action buttons (wrapping FlowLayout) stay LEFT; the collapse chevron is pushed to the
        # FAR RIGHT via the stretch — visually separated from the Run/Walk-forward/… cluster.
        toolbar_row = QtWidgets.QWidget()
        tr = QtWidgets.QHBoxLayout(toolbar_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(_GAP)
        tr.addWidget(toolbar_flow, 1)
        tr.addWidget(self._btn_collapse_top, 0, QtCore.Qt.AlignTop | QtCore.Qt.AlignRight)
        ep.addWidget(toolbar_row)
        ep.addWidget(self.editor, 1)

        # Bottom: two half-width cards — AI Studio chat (left) | code editor (right).
        self._bottom = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self._bottom.addWidget(self.chat)
        self._bottom.addWidget(editor_pane)
        self._bottom.setStretchFactor(0, 1)
        self._bottom.setStretchFactor(1, 1)
        self._bottom.setSizes([1000, 1000])
        self._bottom.setHandleWidth(_GAP)      # inter-card gutter == the uniform outer gap
        self._bottom.setMinimumHeight(_PANE_MIN)  # small floor so the chart can grow / cards shrink smoothly
        self._bottom.setCollapsible(0, True)   # chat (left) may collapse
        self._bottom.setCollapsible(1, False)  # editor (right) is the primary work area

        # Top: the tabbed results (Equity | Performance | Trades | Runs | Distribution [| Chart]).
        # mount_chart() adds the price chart as the trailing "Chart" tab. Reports/chart on top,
        # editor | AI-studio chat below — one tab strip up top, two cards below.
        self._vsplit = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._vsplit.addWidget(self.results)
        self._vsplit.addWidget(self._bottom)
        self._vsplit.setHandleWidth(_GAP)      # top↔bottom gutter == the uniform outer gap
        # Default ~44% top (chart/report) / ~56% bottom (editor + AI studio) — give the cards room.
        # Why the top used to drift past 44%: ResultsPanel's sizeHint is ~510px tall because its
        # EquityChart (a pg.PlotWidget / QGraphicsView) advertises a 640×480 sizeHint. On the
        # deferred first-show layout pass the splitter blends the requested sizes with each child's
        # sizeHint (weighted by stretch), and that tall top hint pulled the divider toward 50/50.
        # Fix: cap the top's preferred height so it can't out-vote the bottom, use large
        # proportional sizes so the absolute request dominates, and re-assert the ratio once after
        # the first real geometry arrives (see _apply_vsplit_ratio).
        # Smooth drag-resize: give each pane a SMALL positive minimum height. A 0 minimum makes
        # Qt's qSmartMinSize fall back to the child's minimumSizeHint (the EquityChart's ~480px),
        # so dragging the divider past ~that height snapped the chart straight to collapsed ("hides
        # past 50%"). A small explicit floor (keeps the tab strip visible) lets the user shrink the
        # chart all the way down smoothly; full hide is the toggle chevron's job.
        self.results.setMinimumHeight(_PANE_MIN)
        self.results.setMaximumHeight(16_777_215)  # no hard cap on drag; only the hint is tamed
        _hint = self.results.sizePolicy()
        _hint.setVerticalStretch(44)               # advertise the 44:56 split as the size policy too
        self.results.setSizePolicy(_hint)
        _bsp = self._bottom.sizePolicy()
        _bsp.setVerticalStretch(56)
        self._bottom.setSizePolicy(_bsp)
        self._vsplit.setSizes([4400, 5600])
        self._vsplit.setStretchFactor(0, 44)
        self._vsplit.setStretchFactor(1, 56)
        # The results/chart panel (top) can be shrunk all the way down — drag the divider up to
        # give the editor + chat the full height, or hide the chart/report entirely.
        self._vsplit.setCollapsible(0, True)
        self._vsplit.setCollapsible(1, True)
        root.addWidget(self._vsplit, stretch=1)
        # Keep the collapse chevron in sync with manual divider drags (not just button clicks):
        # drag the chart down to hidden and the arrow flips to "down"; drag it back and it flips up.
        self._vsplit.splitterMoved.connect(self._on_splitter_moved)

        self.chat.promptSubmitted.connect(self._on_prompt)
        self.chat.providerChanged.connect(self._on_ai_provider_changed)
        self.chat.cerebrasKeyChanged.connect(self._on_cerebras_key_changed)
        self.chat.connectRequested.connect(self._open_connect_dialog)
        self._load_ai_settings()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 - Qt override
        """Re-assert the 44:56 vertical split once the tab first gets real geometry — the
        deferred first-show layout can otherwise equilibrate toward 50/50 before the window is
        sized. Deferred to the event loop so the splitter has its final height when we re-apply."""
        super().showEvent(event)
        if not self._ratio_applied:
            self._ratio_applied = True
            QtCore.QTimer.singleShot(0, self._apply_vsplit_ratio)

    def _apply_vsplit_ratio(self, top_frac: float = 0.44, _tries: int = 6) -> None:
        """Force the vertical splitter to ~44% top / ~56% bottom for the current height.

        Re-tries on the next event-loop tick while the layout is still settling (a transient
        bottom-pane minimum can clamp the first attempt below target right after first show)."""
        if self._top_collapsed:
            return
        total = sum(self._vsplit.sizes())
        if total <= 0:
            if _tries > 0:
                QtCore.QTimer.singleShot(16, lambda: self._apply_vsplit_ratio(top_frac, _tries - 1))
            return
        top = int(round(total * top_frac))
        self._vsplit.setSizes([top, total - top])
        # If a still-settling layout clamped us well off target, try once more next tick.
        got = self._vsplit.sizes()[0]
        if _tries > 0 and abs(got - top) > 0.05 * total:
            QtCore.QTimer.singleShot(16, lambda: self._apply_vsplit_ratio(top_frac, _tries - 1))

    def _toggle_top_panel(self) -> None:
        """Toggle the top chart+report panel based on its ACTUAL current height (so it works the
        same whether the chart was hidden by this button or by dragging the divider): if it's
        hidden, restore it (and the bottom cards shift back down to make room); if it's shown,
        remember its height and hide it (handing the full height to the cards)."""
        sizes = self._vsplit.sizes()
        total = sum(sizes)
        if sizes[0] <= _PANE_MIN:               # hidden / minimal -> bring the chart back
            top = self._saved_top_size or int(total * 0.44)
            self._vsplit.setSizes([top, max(total - top, 0)])
        else:                                   # shown -> remember height, then hide
            self._saved_top_size = sizes[0]
            self._vsplit.setSizes([0, total])
        self._sync_collapse_icon()

    def _on_splitter_moved(self, _pos: int = 0, _index: int = 0) -> None:
        """User dragged the divider: remember the chart height while it's visible (so the toggle
        restores to it) and keep the chevron arrow pointing the right way."""
        top = self._vsplit.sizes()[0]
        if top > _PANE_MIN:
            self._saved_top_size = top
        self._sync_collapse_icon()

    def _sync_collapse_icon(self) -> None:
        """Point the chevron down when the chart is hidden (click to show), up when it's shown
        (click to hide) — kept in sync with both the toggle and manual divider drags."""
        self._top_collapsed = self._vsplit.sizes()[0] <= _PANE_MIN
        name = "chevron_down" if self._top_collapsed else "chevron_up"
        self._btn_collapse_top.setIcon(icons.glyph_icon(name, theme.TEXT2))

    # --- hosted panels (moved in from the Chart space / right dock) ---

    def mount_controls(self, bar: QtWidgets.QWidget) -> None:
        """Merge the replay/data control bar into the Studio toolbar as one row.

        Appended after the Studio action buttons; the bar's trailing slider takes the row's
        stretch, so the Studio toolbar's own trailing stretch is dropped first.
        """
        last = self._toolbar.count() - 1
        item = self._toolbar.itemAt(last)
        if item is not None and item.spacerItem() is not None:
            self._toolbar.takeAt(last)
        self._toolbar.addWidget(bar, 1)

    def mount_bots(self, bots: QtWidgets.QWidget) -> None:
        """Host the Bots panel as the leftmost card of the bottom row (moved from a dock)."""
        self._bottom.insertWidget(0, bots)
        self._bottom.setCollapsible(0, True)

    def mount_chart(self, chart: QtWidgets.QWidget) -> None:
        """Host the price chart (with its playback controls) as the 'Chart' tab in the results,
        after Distribution — reports and the chart switch via the one top tab strip."""
        self.results.mount_chart_tab(chart)

    # --- state setters ---

    def set_bars(self, bars: list) -> None:
        """Set bar data used by Run and the AI agent."""
        self._bars = bars

    def set_config(self, config) -> None:
        """Set TesterConfig for runs."""
        self._config = config

    def set_agent_client(self, client) -> None:
        """Set AI client (ClaudeClient); pass None to disable AI."""
        self._agent_client = client

    def set_text(self, code: str) -> None:
        """Set editor text."""
        self.editor.setText(code)

    def text(self) -> str:
        """Return editor text."""
        return self.editor.text()

    def current_strategy_cls(self):
        """Compile the strategy currently in the editor; return its class, or None if it doesn't compile."""
        from vike_trader_app.core.strategy_loader import load_strategy_from_string
        try:
            return load_strategy_from_string(self.editor.text(), validate=True)
        except Exception:  # noqa: BLE001 - an empty/invalid editor just yields no strategy
            return None

    def show_portfolio_report(self, report, name: str = "", *,
                              bars_by_symbol=None, ranges=None) -> None:
        """Display a portfolio backtest report in the results panel (no per-bar price chart).

        ``name`` (the DataSet) is surfaced in the results toast so the user sees which universe ran.
        When ``bars_by_symbol`` is provided the Studio enters portfolio-optimize mode: subsequent
        Walk-forward presses will optimize across the whole DataSet via ``PortfolioStrategyTester``
        instead of the single-symbol path.
        """
        if bars_by_symbol is not None:
            self._portfolio_bars = bars_by_symbol
            self._portfolio_ranges = ranges
            self._portfolio_name = name
        self.results.add_run(report, [], {})
        if name:
            self.results.toast(f"✓ Portfolio · {name}")

    # --- run ---

    def run_code(self) -> None:
        """Load the strategy from the editor and run a single backtest, recording it.

        Honors the per-run config (starting capital + date-range slice) set via the
        Settings modal; falls back to the full bars + the tab's TesterConfig otherwise.
        A single-symbol run exits portfolio-optimize mode so the Walk-forward button follows
        the latest action.
        """

        from vike_trader_app.core.strategy_loader import load_strategy_from_string
        from vike_trader_app.tester import StrategyTester, TesterConfig

        self._portfolio_bars = None  # exit portfolio mode on a single-symbol run
        code = self.editor.text()
        config = self._config if self._config is not None else TesterConfig()
        if self._run_capital is not None:
            config = replace(config, cash=self._run_capital)
        bars = self._effective_bars()
        try:
            cls = load_strategy_from_string(code, validate=True)
            report = StrategyTester(cls(), bars, config).run()
            overlays = {}
            try:
                overlays = cls().chart_overlays([b.close for b in bars]) or {}
            except Exception:  # noqa: BLE001 - overlays are optional, never block the run
                overlays = {}
            self.results.add_run(report, bars, overlays)
        except Exception as exc:  # noqa: BLE001
            self.results.show_error(f"{type(exc).__name__}: {exc}")

    def _effective_bars(self) -> list:
        """Apply the per-run date slice + resolution resample to the loaded base bars."""
        bars = self._bars
        if self._run_range is not None:
            s, e = self._run_range
            bars = [b for b in self._bars if s <= b.ts <= e] or self._bars
        if self._run_resolution is not None and bars:
            from vike_trader_app.core.timeframe import resample
            coarse = resample(bars, self._run_resolution)
            if len(coarse) >= 2:           # never resample down to an unusable handful
                bars = coarse
        return bars

    def _open_templates(self) -> None:
        """Open the strategy-template gallery; chosen template loads into the editor."""
        from .templates import StrategyTemplateDialog

        dlg = StrategyTemplateDialog(parent=self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)  # don't accumulate dialogs across opens
        dlg.loadRequested.connect(self._load_template)
        dlg.exec()

    def _load_template(self, code: str) -> None:
        if self.editor.text().strip():
            ok = QtWidgets.QMessageBox.question(
                self, "Load template", "Replace the current editor contents with this template?"
            )
            if ok != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self.editor.setText(code)

    def _optimize(self) -> None:
        """Walk-forward optimize the strategy's PARAM_GRID; show the stitched OOS run + overfit verdict.

        This is the "optimize safely" path: parameters are picked per train window and scored
        OUT-OF-SAMPLE, and the stitched result carries a PBO/deflated-Sharpe overfit verdict (the
        banner) — so a curve-fit grid sweep shows up as High overfit risk rather than a shiny number.
        """
        from vike_trader_app.core.strategy_loader import load_strategy_from_string
        from vike_trader_app.tester import StrategyTester, TesterConfig

        config = self._config if self._config is not None else TesterConfig()
        if self._run_capital is not None:
            config = replace(config, cash=self._run_capital)
        bars = self._effective_bars()
        try:
            cls = load_strategy_from_string(self.editor.text(), validate=True)
        except Exception as exc:  # noqa: BLE001
            self.results.show_error(f"{type(exc).__name__}: {exc}")
            return
        grid = getattr(cls, "PARAM_GRID", {}) or {}
        if not grid:
            self.results.toast("Add a PARAM_GRID to the strategy to walk-forward optimize it.")
            return
        cfg = self._opt_config
        wf_kw = asdict(cfg)   # OptimizerConfig fields == walk_forward kwargs
        if self._portfolio_bars:
            from vike_trader_app.tester.portfolio_tester import PortfolioStrategyTester
            n = min((len(b) for b in self._portfolio_bars.values()), default=0)
            if n < 120:
                self.results.toast("Need ≥120 bars per symbol to walk-forward optimize a portfolio.")
                return
            self.results.toast(f"Portfolio walk-forward optimizing {self._portfolio_name}…")
            QtWidgets.QApplication.processEvents()
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                pt = PortfolioStrategyTester(self._portfolio_bars, config, ranges=self._portfolio_ranges)
                wf = pt.walk_forward(cls.make, grid, **wf_kw)
            except Exception as exc:  # noqa: BLE001
                self.results.show_error(f"Portfolio optimize failed: {type(exc).__name__}: {exc}")
                return
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            self.results.add_run(wf.oos_report, [])    # portfolio: no per-bar price chart
            self.results.show_walk_forward(wf, cfg.criterion)
            self._populate_surface(pt, cls.make, grid, cfg.criterion)
            best = wf.windows[-1].best_params if wf.windows else {}
            level = wf.oos_report.verdict.level if wf.oos_report.verdict else "?"
            self.results.toast(f"Portfolio WF-OOS · {self._portfolio_name} · overfit: {level} · best {best}")
            return
        if len(bars) < 120:
            self.results.toast("Need ≥120 bars to walk-forward optimize.")
            return
        self.results.toast("Optimizing + walk-forward validating…")
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            st = StrategyTester(cls(), bars, config)
            wf = st.walk_forward(cls.make, grid, **wf_kw)
        except Exception as exc:  # noqa: BLE001
            self.results.show_error(f"Optimize failed: {type(exc).__name__}: {exc}")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.results.add_run(wf.oos_report, bars)
        self.results.show_walk_forward(wf, cfg.criterion)
        self._populate_surface(st, cls.make, grid, cfg.criterion)
        best = wf.windows[-1].best_params if wf.windows else {}
        level = wf.oos_report.verdict.level if wf.oos_report.verdict else "?"
        self.results.toast(f"Walk-forward OOS · overfit risk: {level} · best {best}")

    def _populate_surface(self, optimizer, make, grid, criterion) -> None:
        """Feed the Surface tab from an in-sample GRID optimize over ≤2 axes (capped, exhaustive).

        The surface is the optimization LANDSCAPE — always a full grid (so neighbours are
        comparable), independent of the walk-forward search method. Skipped (with a hint) when the
        grid has <2 multi-valued params or exceeds _SURFACE_MAX_COMBOS backtests.
        """
        multi = [k for k, v in grid.items() if len(v) >= 2]
        combos = 1
        for v in grid.values():
            combos *= len(v)
        if len(multi) < 2 or combos > _SURFACE_MAX_COMBOS:
            self.results.show_surface([], grid, criterion)  # shows the "needs a ≥2-param grid" hint
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)  # the grid sweep blocks; show busy
        try:
            rep = optimizer.optimize(make, grid, criterion=criterion, method="grid")
        except Exception:  # noqa: BLE001 - surface is a nice-to-have, never block the WF result
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.results.show_surface(rep.ranked, grid, criterion)

    def _open_optimizer_config(self) -> None:
        """Open the optimizer modal (search method, criterion, walk-forward mode) and store it."""
        dlg = OptimizerConfigDialog(self._opt_config, parent=self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self._opt_config = dlg.values()
            c = self._opt_config
            self.results.toast(
                f"Optimizer · {c.method} · {c.criterion} · WF {c.mode} ({c.n_splits}) — press Walk-forward"
            )

    def _export_csv(self) -> None:
        """Export the current run's metrics + trades to a CSV file."""
        report = self.results.last_report
        if report is None:
            self.results.toast("Run a backtest first — nothing to export.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export report CSV", "report.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(report_extras.report_to_csv(report))
            self.results.toast(f"Exported → {path}")
        except OSError as exc:
            self.results.show_error(f"Export failed: {exc}")

    def _open_config(self) -> None:
        """Open the per-run backtest-config modal (capital + date range)."""
        from vike_trader_app.tester import TesterConfig

        if not self._bars:
            QtWidgets.QMessageBox.information(self, "Backtest settings", "Load data first.")
            return
        cap = self._run_capital
        if cap is None:
            cap = (self._config.cash if self._config is not None else TesterConfig().cash)
        dlg = BacktestConfigDialog(self._bars, capital=cap, parent=self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)  # freed after exec returns (deferred delete)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            bc = dlg.values()
            self._run_capital = bc.capital
            self._run_range = (bc.start_ts, bc.end_ts)
            self._run_resolution = bc.resolution_ms
            res_lbl = dlg.resolution.value() or "base"
            self.results.toast(
                f"Settings · capital ${bc.capital:,.0f} · {res_lbl} · range set — press Run"
            )

    # --- AI provider / connect-to-Claude ---

    def _ai_settings(self):
        return QtCore.QSettings("vike-trader", "vike-trader-app")

    def _load_ai_settings(self) -> None:
        """Restore the saved provider + Cerebras key and build the initial AI client (silently)."""
        s = self._ai_settings()
        self.chat.set_provider(str(s.value("ai/provider", "claude")))
        self.chat.set_cerebras_key(str(s.value("ai/cerebras_key", "") or ""))
        self._rebuild_agent_client()

    def _on_ai_provider_changed(self, provider: str) -> None:
        self._ai_settings().setValue("ai/provider", provider)
        self._rebuild_agent_client(announce=True)

    def _on_cerebras_key_changed(self, key: str) -> None:
        self._ai_settings().setValue("ai/cerebras_key", key)
        if self.chat.provider() == "cerebras":
            self._rebuild_agent_client(announce=True)

    def _rebuild_agent_client(self, *, announce: bool = False) -> None:
        """Construct the LLM client for the selected provider (BYO key); None -> graceful no-AI."""
        import os

        provider = self.chat.provider()
        client = None
        note = ""
        try:
            if provider == "cerebras":
                key = self.chat.cerebras_key() or os.environ.get("CEREBRAS_API_KEY")
                if key:
                    from vike_trader_app.ai.llm import CerebrasClient
                    client = CerebrasClient(api_key=key)
                else:
                    note = "Enter your Cerebras API key above to enable the AI assistant."
            elif os.environ.get("ANTHROPIC_API_KEY"):
                from vike_trader_app.ai.llm import ClaudeClient
                client = ClaudeClient()
            else:
                note = "Set ANTHROPIC_API_KEY to use Claude, or switch to Cerebras and paste a key."
        except Exception as exc:  # noqa: BLE001 - missing extra / bad config -> graceful no-AI mode
            client, note = None, f"AI unavailable: {exc}"
        self._agent_client = client
        if announce and note:
            self.chat.append_message("system", note)
        elif announce and client is not None:
            self.chat.append_message("system", f"AI provider set to {provider.capitalize()}.")

    def _open_connect_dialog(self) -> None:
        """Write the MCP server entry into Claude Desktop's config + offer the Claude Code command.

        When a telemetry endpoint is configured (connect.DEFAULT_TELEMETRY_URL or VIKE_TELEMETRY_URL),
        an opt-in consent checkbox (default OFF, for GDPR/CCPA) controls whether the locally-spawned
        MCP server reports anonymous usage. With no endpoint set, no checkbox appears and telemetry
        stays off.
        """
        from vike_trader_app.ai import connect

        url = connect.telemetry_url()
        s = self._ai_settings()
        consent = str(s.value("ai/telemetry_consent", "false")).lower() in ("true", "1", "yes")

        def _install(send: bool):
            data_root = connect.default_data_root()
            kw = {"telemetry": send and bool(url), "telemetry_url": url or None}
            return (connect.install_into_claude_desktop(data_root, **kw),
                    connect.claude_code_command_str(data_root, **kw))

        try:
            cfg_path, cmd = _install(consent)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Connect to Claude", f"Couldn't configure Claude: {exc}")
            return

        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Connect to Claude")
        box.setIcon(QtWidgets.QMessageBox.Information)
        box.setTextFormat(QtCore.Qt.RichText)
        box.setText(
            "Added <b>vike-trader</b> to Claude Desktop:<br>"
            f"<span style='color:gray;font-size:11px'>{cfg_path}</span><br><br>"
            "Restart Claude Desktop, then ask it to backtest or optimise your strategies — it runs on "
            "<b>your own Claude Pro/Max subscription</b>.<br><br>"
            "Prefer Claude Code? Copy the command below and run it in your terminal."
        )
        cb = None
        if url:
            cb = QtWidgets.QCheckBox(
                "Share anonymous usage with vike.io (tool calls + timing — never your strategy code)"
            )
            cb.setChecked(consent)
            box.setCheckBox(cb)
        btn_open = box.addButton("Open Claude Desktop", QtWidgets.QMessageBox.AcceptRole)
        btn_copy = box.addButton("Copy Claude Code command", QtWidgets.QMessageBox.ActionRole)
        box.addButton("Close", QtWidgets.QMessageBox.RejectRole)
        box.exec()

        if cb is not None and cb.isChecked() != consent:
            consent = cb.isChecked()
            s.setValue("ai/telemetry_consent", consent)
            try:
                cfg_path, cmd = _install(consent)  # rewrite config with the chosen telemetry setting
            except Exception:  # noqa: BLE001
                pass

        clicked = box.clickedButton()
        if clicked is btn_copy:
            QtWidgets.QApplication.clipboard().setText(cmd)
        elif clicked is btn_open and not connect.launch_claude_desktop():
            QtWidgets.QMessageBox.information(
                self, "Connect to Claude",
                "Couldn't find Claude Desktop automatically — please open it manually. "
                "vike-trader is already configured.",
            )

    # --- chat ---

    def _on_prompt(self, prompt: str) -> None:
        if self._agent_client is None:
            self.chat.append_message("system", "No AI client configured.")
            return
        if self._worker is not None and self._worker.isRunning():
            self.chat.append_message("system", "AI is still working — please wait…")
            return

        from vike_trader_app.tester import TesterConfig
        config = self._config if self._config is not None else TesterConfig()
        worker = ChatWorker(self._agent_client, prompt, self._bars, config)
        self._worker = worker
        worker.result.connect(self._on_agent_result)
        worker.finished.connect(self._on_worker_finished)
        self.chat.set_busy(True)
        self.chat.append_message("user", prompt)
        worker.start()

    def _on_agent_result(self, res) -> None:
        if res.code:
            current = self.editor.text()
            if current.strip():
                # human-in-the-loop: review the AI's change as a diff before applying
                self._apply_version += 1
                dlg = DiffDialog(current, res.code, self._apply_version, parent=self)
                dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
                if dlg.exec() == QtWidgets.QDialog.Accepted:
                    self.editor.setText(res.code)
                    self.chat.append_message("system", f"Applied AI change — v{self._apply_version}.")
                else:
                    self._apply_version -= 1
                    self.chat.append_message("system", "AI change rejected.")
            else:
                self.editor.setText(res.code)  # empty editor -> just load it
        if res.explanation:
            self.chat.append_message("assistant", res.explanation)
        elif not res.accepted and res.problems:
            self.chat.append_message("system", "Agent failed: " + "; ".join(res.problems))

    def _on_worker_finished(self) -> None:
        """Release the finished worker (only now is the QThread truly done) + re-enable input."""
        self.chat.set_busy(False)
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()

    def shutdown(self) -> None:
        """Wait for any in-flight AI worker so we never destroy a running QThread on close."""
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.wait(3000)
