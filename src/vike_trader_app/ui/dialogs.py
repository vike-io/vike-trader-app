"""Data-load dialog: pull cached/deep history from Binance or open a local Parquet file.

Also exposes a default demo strategy (SMA crossover) so the app has something to run
out of the box. Users will supply their own strategies later.
"""

import time

from PySide6 import QtCore, QtWidgets

from ..core.indicators import sma
from ..core.strategy import Strategy
from ..data.cache import get_bars
from ..data.parquet_source import read_bars_parquet

_DAY_MS = 86_400_000


class SmaCross(Strategy):
    """Demo strategy: long when fast SMA crosses above slow SMA, flat otherwise."""

    fast = 10
    slow = 30
    PARAM_GRID = {"fast": [5, 10, 15], "slow": [20, 40, 60]}

    def __init__(self):
        super().__init__()
        self._closes: list[float] = []

    def on_bar(self, bar):
        self._closes.append(bar.close)
        if len(self._closes) <= self.slow:
            return
        fast = sum(self._closes[-self.fast :]) / self.fast
        slow = sum(self._closes[-self.slow :]) / self.slow
        if fast > slow and self.position.size == 0:
            self.buy(0.01)
        elif fast < slow and self.position.size > 0:
            self.close()

    def chart_overlays(self, closes):
        return {
            f"SMA{self.fast}": sma(closes, self.fast),
            f"SMA{self.slow}": sma(closes, self.slow),
        }


def default_strategy_factory():
    return SmaCross


class LoadDataDialog(QtWidgets.QDialog):
    """Choose a data source + history depth; on accept, ``self.bars`` holds the bars."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Load data")
        self.setMinimumWidth(360)
        self.bars = []

        form = QtWidgets.QFormLayout(self)
        self.source = QtWidgets.QComboBox()
        self.source.addItems(["Binance (cached history)", "Parquet file"])
        self.symbol = QtWidgets.QLineEdit("BTCUSDT")
        self.interval = QtWidgets.QComboBox()
        self.interval.addItems(["1m", "5m", "15m", "1h", "4h", "1d"])
        self.days = QtWidgets.QSpinBox()
        self.days.setRange(1, 3650)
        self.days.setValue(30)
        self.days.setSuffix(" days back")
        self.path = QtWidgets.QLineEdit()
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QtWidgets.QHBoxLayout()
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)

        form.addRow("Source:", self.source)
        form.addRow("Symbol:", self.symbol)
        form.addRow("Interval:", self.interval)
        form.addRow("History:", self.days)
        form.addRow("Parquet path:", path_row)

        self.status = QtWidgets.QLabel("Cached locally — only new bars are downloaded.")
        self.status.setWordWrap(True)
        form.addRow(self.status)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._load)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Parquet", "", "Parquet (*.parquet)"
        )
        if path:
            self.path.setText(path)

    def _progress(self, done, start, end):
        pct = (done - start) / max(end - start, 1) * 100
        self.status.setText(f"Fetching… {pct:.0f}%")
        QtWidgets.QApplication.processEvents()

    def _load(self):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            if self.source.currentIndex() == 0:
                now = int(time.time() * 1000)
                start = now - self.days.value() * _DAY_MS
                self.bars = get_bars(
                    self.symbol.text().strip(),
                    self.interval.currentText(),
                    start,
                    now,
                    progress=self._progress,
                )
            else:
                self.bars = read_bars_parquet(self.path.text().strip())
        except Exception as exc:  # noqa: BLE001 - surface any load error to the user
            self.status.setText(f"Load failed: {exc}")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if not self.bars:
            self.status.setText("No bars loaded.")
            return
        self.accept()
