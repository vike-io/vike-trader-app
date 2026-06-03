"""GUI optimizer panel: run a param sweep, rank results, show the Sharpe heatmap
and a 4-chart dashboard of the best run.

Matches vnpy's GUI-driven optimizer (param grid + objective + sortable results) and
vectorbt's iconic 2-param heatmap — but free, English, crypto-first. All heavy logic
lives in Qt-free modules (`analysis.optimizer`, `ui.dashboard_data`); this file only
wires widgets.
"""

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from ..analysis.metrics import sharpe, total_return
from ..analysis.optimizer import grid_search
from ..core.engine import BacktestEngine
from . import dashboard_data as dd
from . import theme

_EQUITY = theme.ACCENT
_OBJECTIVES = {
    "Sharpe": lambda r: sharpe(r.equity_curve),
    "Net return": lambda r: total_return(r.equity_curve),
    "Final equity": lambda r: r.final_equity,
}


def show_optimizer(parent, bars, strategy_cls, fee_rate: float = 0.001) -> None:
    """Open the optimizer panel for ``strategy_cls`` over ``bars``."""
    grid = getattr(strategy_cls, "PARAM_GRID", {})
    if not grid:
        QtWidgets.QMessageBox.information(
            parent, "Optimizer",
            f"{strategy_cls.__name__} declares no PARAM_GRID — nothing to sweep.",
        )
        return
    OptimizerDialog(parent, bars, strategy_cls, fee_rate).exec()


class OptimizerDialog(QtWidgets.QDialog):
    """Param-range sweep + objective selector + sortable results + heatmap + dashboard."""

    def __init__(self, parent, bars, strategy_cls, fee_rate: float = 0.001):
        super().__init__(parent)
        self.bars, self.strategy_cls, self.fee_rate = bars, strategy_cls, fee_rate
        self.grid = dict(strategy_cls.PARAM_GRID)
        self.keys = list(self.grid)
        self.setWindowTitle(f"Optimizer — {strategy_cls.__name__}")
        self.resize(1040, 720)

        root = QtWidgets.QVBoxLayout(self)

        # --- controls ---
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel("Objective:"))
        self.objective = QtWidgets.QComboBox()
        self.objective.addItems(list(_OBJECTIVES))
        controls.addWidget(self.objective)
        run = QtWidgets.QPushButton("Run sweep")
        run.clicked.connect(self.run_sweep)
        controls.addWidget(run)
        controls.addStretch(1)
        root.addLayout(controls)

        body = QtWidgets.QHBoxLayout()
        root.addLayout(body, 1)

        # --- results table (sortable) ---
        self.table = QtWidgets.QTableWidget(0, len(self.keys) + 1)
        self.table.setHorizontalHeaderLabels([*self.keys, "score"])
        self.table.setSortingEnabled(True)
        self.table.cellClicked.connect(self._row_selected)
        body.addWidget(self.table, 1)

        # --- heatmap ---
        self.heatmap = pg.ImageView()
        self.heatmap.ui.histogram.hide()
        self.heatmap.ui.roiBtn.hide()
        self.heatmap.ui.menuBtn.hide()
        body.addWidget(self.heatmap, 1)

        # --- 4-chart dashboard ---
        charts = QtWidgets.QGridLayout()
        self.p_equity = pg.PlotWidget(title="Equity")
        self.p_dd = pg.PlotWidget(title="Drawdown")
        self.p_pnl = pg.PlotWidget(title="Per-bar P&L")
        self.p_hist = pg.PlotWidget(title="Return distribution")
        charts.addWidget(self.p_equity, 0, 0)
        charts.addWidget(self.p_dd, 0, 1)
        charts.addWidget(self.p_pnl, 1, 0)
        charts.addWidget(self.p_hist, 1, 1)
        root.addLayout(charts, 1)

        self._results = []

    # --- actions ---
    def run_sweep(self) -> None:
        score_fn = _OBJECTIVES[self.objective.currentText()]
        self._results = grid_search(self.bars, self.strategy_cls.make, self.grid, score_fn=score_fn, fee_rate=self.fee_rate)
        self._fill_table()
        self._draw_heatmap(score_fn)
        if self._results:
            self._show_best(self._results[0])

    def _fill_table(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._results))
        for row, res in enumerate(self._results):
            for col, key in enumerate(self.keys):
                self.table.setItem(row, col, QtWidgets.QTableWidgetItem(str(res.params[key])))
            score_item = QtWidgets.QTableWidgetItem()
            score_item.setData(QtCore.Qt.EditRole, float(res.score))  # numeric sort
            self.table.setItem(row, len(self.keys), score_item)
        self.table.setSortingEnabled(True)

    def _draw_heatmap(self, score_fn) -> None:
        if len(self.keys) < 2:
            return
        xk, yk = self.keys[0], self.keys[1]
        fixed = {k: self.grid[k][0] for k in self.keys[2:]}

        def make2(**kw):
            return self.strategy_cls.make(**{**fixed, **kw})

        _, _, scores = dd.sharpe_heatmap(
            self.bars, make2, xk, self.grid[xk], yk, self.grid[yk], score_fn=score_fn, fee_rate=self.fee_rate
        )
        arr = np.array(scores, dtype=float).T  # (x, y) for ImageView
        self.heatmap.setImage(arr, autoLevels=True)
        self.heatmap.setColorMap(pg.colormap.get("inferno"))

    def _row_selected(self, row, _col) -> None:
        if 0 <= row < len(self._results):
            # map the (possibly re-sorted) visible row back via the score column
            score = self.table.item(row, len(self.keys)).data(QtCore.Qt.EditRole)
            match = next((r for r in self._results if abs(r.score - score) < 1e-12), None)
            if match:
                self._show_best(match)

    def _show_best(self, res) -> None:
        run = BacktestEngine(self.bars, self.strategy_cls.make(**res.params), fee_rate=self.fee_rate).run()
        eq = run.equity_curve
        self.p_equity.clear(); self.p_equity.plot(eq, pen=_EQUITY)
        self.p_dd.clear(); self.p_dd.plot(dd.drawdown_curve(eq), pen=theme.DOWN)
        self.p_pnl.clear()
        pnl = dd.per_bar_pnl(eq)
        self.p_pnl.addItem(pg.BarGraphItem(x=list(range(len(pnl))), height=pnl, width=0.8, brush=theme.UP))
        self.p_hist.clear()
        centers, counts = dd.return_histogram(eq)
        if centers:
            self.p_hist.addItem(pg.BarGraphItem(x=centers, height=counts, width=(centers[-1] - centers[0]) / max(len(centers), 1) or 1, brush=_EQUITY))
