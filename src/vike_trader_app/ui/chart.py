"""pyqtgraph charts: a candlestick price chart with trade markers + indicator
overlays, plus an equity curve. Both plot by **bar index** and support progressive
reveal (`show_upto`) so the replay hides future bars like MT5's visual tester.
"""

import pyqtgraph as pg
from PySide6 import QtCore, QtGui
from PySide6.QtCore import QRectF

from . import theme
from .chartdata import follow_window, trade_markers, y_bounds

_UP = theme.UP
_DOWN = theme.DOWN
_ENTRY = theme.UP
_EXIT = theme.DOWN
_OVERLAY_COLORS = [theme.FAST, theme.SLOW, "#26c6da", "#66bb6a", "#ec407a"]
_GRID = 0.12


class CandlestickItem(pg.GraphicsObject):
    """Draws OHLC candles for ``bars`` (a list of core.model.Bar)."""

    def __init__(self, bars):
        super().__init__()
        self._bars = bars
        self._picture = QtGui.QPicture()
        self._generate()

    def set_bars(self, bars):
        self._bars = bars
        self._generate()
        self.informViewBoundsChanged()
        self.update()

    def _generate(self):
        self._picture = QtGui.QPicture()
        if not self._bars:
            return
        painter = QtGui.QPainter(self._picture)
        width = 0.6
        for i, b in enumerate(self._bars):
            color = QtGui.QColor(_UP if b.close >= b.open else _DOWN)
            painter.setPen(pg.mkPen(color))
            painter.setBrush(pg.mkBrush(color))
            painter.drawLine(pg.Point(i, b.low), pg.Point(i, b.high))
            top, bottom = max(b.open, b.close), min(b.open, b.close)
            painter.drawRect(QRectF(i - width / 2, bottom, width, max(top - bottom, 1e-9)))
        painter.end()

    def paint(self, painter, *_):
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self):
        if not self._bars:
            return QRectF(0, 0, 1, 1)
        lo = min(b.low for b in self._bars)
        hi = max(b.high for b in self._bars)
        return QRectF(-1, lo, len(self._bars) + 1, hi - lo)


class PriceChart(pg.PlotWidget):
    """Candles + entry/exit markers + indicator overlays + a replay cursor."""

    def __init__(self):
        super().__init__()
        self.setBackground(theme.BG)
        self.showGrid(x=True, y=True, alpha=_GRID)
        self.getAxis("left").setTextPen(theme.TEXT3)
        self.getAxis("bottom").setTextPen(theme.TEXT3)
        self.addLegend(offset=(10, 8), labelTextColor=theme.TEXT2)
        self._bars = []
        self._window = 300  # default candles shown; user can mouse-zoom out
        self._follow = True  # keep the replay cursor in view
        self._entries = []
        self._exits = []
        self._overlays = {}  # label -> full series (aligned to bars)
        self._overlay_curves = {}  # label -> PlotDataItem

        self._candles = CandlestickItem([])
        self.addItem(self._candles)
        self._entry_scatter = pg.ScatterPlotItem(
            symbol="t1", size=14, brush=pg.mkBrush(_ENTRY), pen=None, name="entry"
        )
        self._exit_scatter = pg.ScatterPlotItem(
            symbol="t", size=14, brush=pg.mkBrush(_EXIT), pen=None, name="exit"
        )
        self.addItem(self._entry_scatter)
        self.addItem(self._exit_scatter)
        self._cursor = pg.InfiniteLine(angle=90, pen=pg.mkPen(theme.ACCENT, width=1))
        self.addItem(self._cursor)
        self._cursor.hide()

    def set_data(self, bars, trades):
        self._bars = bars
        ts_index = {b.ts: i for i, b in enumerate(bars)}
        self._entries, self._exits = [], []
        for m in trade_markers(trades):
            idx = ts_index.get(m.ts)
            if idx is None:
                continue
            (self._entries if m.kind == "entry" else self._exits).append((idx, m.price))
        # the view is set by show_upto -> the default lands on the last ``window`` bars
        self.show_upto(len(bars) - 1)

    def set_overlays(self, overlays: dict):
        """Set indicator overlay lines: ``{label: series aligned to bars}``."""
        for curve in self._overlay_curves.values():
            self.removeItem(curve)
        self._overlay_curves = {}
        self._overlays = overlays or {}
        for i, label in enumerate(self._overlays):
            color = _OVERLAY_COLORS[i % len(_OVERLAY_COLORS)]
            self._overlay_curves[label] = self.plot(
                [], [], pen=pg.mkPen(color, width=1), name=label
            )
        self.show_upto(len(self._bars) - 1 if self._bars else 0)

    def show_upto(self, index: int):
        """Reveal candles/markers/overlays up to and including ``index``."""
        if not self._bars:
            return
        self._candles.set_bars(self._bars[: index + 1])
        self._entry_scatter.setData(
            [i for i, _ in self._entries if i <= index],
            [p for i, p in self._entries if i <= index],
        )
        self._exit_scatter.setData(
            [i for i, _ in self._exits if i <= index],
            [p for i, p in self._exits if i <= index],
        )
        for label, curve in self._overlay_curves.items():
            series = self._overlays.get(label, [])
            xs = [i for i in range(min(index + 1, len(series))) if series[i] is not None]
            ys = [series[i] for i in xs]
            curve.setData(xs, ys)
        self._cursor.show()
        self._cursor.setPos(index)
        if self._follow:
            lo, hi = follow_window(index, len(self._bars), self._window)
            self.setXRange(lo, hi, padding=0.02)
            yb = y_bounds(self._bars, lo, min(hi, index + 1))
            if yb:
                self.setYRange(yb[0], yb[1], padding=0.06)


class EquityChart(pg.PlotWidget):
    """Equity curve over bar index, with progressive reveal for replay."""

    def __init__(self):
        super().__init__()
        self.setBackground(theme.BG)
        self.showGrid(x=True, y=True, alpha=_GRID)
        self.getAxis("left").setTextPen(theme.TEXT3)
        self.getAxis("bottom").setTextPen(theme.TEXT3)
        self._equity = []
        self._curve = self.plot([], [], pen=pg.mkPen(theme.UP, width=2))
        self._baseline = pg.InfiniteLine(
            angle=0, pen=pg.mkPen(theme.TEXT3, width=1, style=QtCore.Qt.DashLine)
        )
        self.addItem(self._baseline)
        self._baseline.hide()

    def set_data(self, equity_curve):
        self._equity = list(equity_curve)
        if self._equity:
            self.setXRange(0, len(self._equity), padding=0.02)
            lo, hi = min(self._equity), max(self._equity)
            self.setYRange(lo, hi, padding=0.1)
            # green if we ended up, red if down; baseline at the starting equity
            up = self._equity[-1] >= self._equity[0]
            self._curve.setPen(pg.mkPen(theme.UP if up else theme.DOWN, width=2))
            self._baseline.setPos(self._equity[0])
            self._baseline.show()
        self.show_upto(len(self._equity) - 1)

    def show_upto(self, index: int):
        if not self._equity:
            return
        n = index + 1
        self._curve.setData(list(range(n)), self._equity[:n])
