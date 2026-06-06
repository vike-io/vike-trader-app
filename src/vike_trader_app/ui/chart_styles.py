"""Price-chart render items + style metadata for the chart-style switch.

The default candle rendering stays on ``chart.CandlestickItem`` (unchanged, proven). This module
adds the extra glyph renderers (hollow/volume candles, OHLC bars, Renko/Range/Line-break blocks,
Kagi, Point & Figure) plus the catalogue of styles and which ones keep a 1:1 time axis vs the
non-time styles (whose bar count differs, so overlays/markers/panes are hidden while active).

Line / area / step / baseline / columns are rendered with stock pyqtgraph items configured in
``chart.py`` — no custom class needed.
"""

import pyqtgraph as pg
from PySide6 import QtCore, QtGui
from PySide6.QtCore import QRectF

from . import theme

# Style catalogue, grouped for the dropdown. NON-TIME styles produce a different number of synthetic
# units (no 1:1 time/index mapping), so the chart hides overlays/markers/panes while they're active.
TIME_STYLES = [
    "Candles", "Hollow candles", "Bars", "HLC bars", "High-low",
    "Line", "Line with markers", "Step line", "Area", "Baseline", "HLC area", "Columns",
    "Heikin Ashi", "Volume candles",
]
NONTIME_STYLES = ["Renko", "Range", "Line break", "Kagi", "Point & Figure"]
ALL_STYLES = TIME_STYLES + NONTIME_STYLES

# Dropdown sections (label, [styles]).
STYLE_SECTIONS = [
    ("Candles & bars", ["Candles", "Hollow candles", "Heikin Ashi", "Volume candles",
                        "Bars", "HLC bars", "High-low"]),
    ("Lines", ["Line", "Line with markers", "Step line", "Area", "Baseline", "HLC area", "Columns"]),
    ("Non-time", NONTIME_STYLES),
]


def is_time_based(style: str) -> bool:
    """True for 1:1 styles (indicators/markers/panes work); False for Renko/Kagi/etc."""
    return style not in NONTIME_STYLES


def family(style: str) -> str:
    """Map a style label to its render family (the renderer that draws it)."""
    return {
        "Candles": "candle", "Heikin Ashi": "candle",
        "Hollow candles": "hollow", "Volume candles": "volcandle",
        "Bars": "bar_ohlc", "HLC bars": "bar_hlc", "High-low": "bar_hl",
        "Line": "line", "Line with markers": "linemark", "Step line": "step",
        "Area": "area", "Baseline": "baseline", "HLC area": "hlcarea", "Columns": "columns",
        "Renko": "block", "Range": "block", "Line break": "block",
        "Kagi": "kagi", "Point & Figure": "pnf",
    }.get(style, "candle")


class CandleItem(pg.GraphicsObject):
    """Candles with optional hollow up-bodies and per-bar widths (for volume candles)."""

    def __init__(self, *, hollow: bool = False):
        super().__init__()
        self._bars: list = []
        self._widths = None
        self._hollow = hollow
        self._picture = QtGui.QPicture()

    def set_bars(self, bars, widths=None):
        self._bars = bars
        self._widths = widths
        self._generate()
        self.informViewBoundsChanged()
        self.update()

    def _generate(self):
        self._picture = QtGui.QPicture()
        if not self._bars:
            return
        p = QtGui.QPainter(self._picture)
        for i, b in enumerate(self._bars):
            up = b.close >= b.open
            color = QtGui.QColor(theme.CANDLE_UP if up else theme.CANDLE_DOWN)
            w = self._widths[i] if self._widths else 0.6
            p.setPen(pg.mkPen(color))
            if b.high > b.low:
                p.drawLine(pg.Point(i, b.low), pg.Point(i, b.high))
            top, bottom = max(b.open, b.close), min(b.open, b.close)
            if top > bottom:
                rect = QRectF(i - w / 2, bottom, w, top - bottom)
                if self._hollow and up:
                    p.setBrush(QtCore.Qt.NoBrush)  # hollow up-body: outline only
                else:
                    p.setBrush(pg.mkBrush(color))
                p.drawRect(rect)
            else:
                p.drawLine(pg.Point(i - w / 2, bottom), pg.Point(i + w / 2, bottom))
        p.end()

    def paint(self, painter, *_):
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self):
        if not self._bars:
            return QRectF(0, 0, 1, 1)
        lo = min(b.low for b in self._bars)
        hi = max(b.high for b in self._bars)
        return QRectF(-1, lo, len(self._bars) + 1, hi - lo)


class BarItem(pg.GraphicsObject):
    """OHLC / HLC / High-low bars. ``mode`` ∈ {'ohlc','hlc','hl'}: open tick (left) only in ohlc,
    close tick (right) in ohlc+hlc, just the high-low line in hl."""

    def __init__(self, mode: str = "ohlc"):
        super().__init__()
        self._bars: list = []
        self._mode = mode
        self._picture = QtGui.QPicture()

    def set_bars(self, bars):
        self._bars = bars
        self._generate()
        self.informViewBoundsChanged()
        self.update()

    def _generate(self):
        self._picture = QtGui.QPicture()
        if not self._bars:
            return
        p = QtGui.QPainter(self._picture)
        tick = 0.32
        for i, b in enumerate(self._bars):
            color = QtGui.QColor(theme.CANDLE_UP if b.close >= b.open else theme.CANDLE_DOWN)
            p.setPen(pg.mkPen(color, width=1.4))
            p.drawLine(pg.Point(i, b.low), pg.Point(i, b.high))
            if self._mode == "ohlc":
                p.drawLine(pg.Point(i - tick, b.open), pg.Point(i, b.open))
            if self._mode in ("ohlc", "hlc"):
                p.drawLine(pg.Point(i, b.close), pg.Point(i + tick, b.close))
        p.end()

    def paint(self, painter, *_):
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self):
        if not self._bars:
            return QRectF(0, 0, 1, 1)
        lo = min(b.low for b in self._bars)
        hi = max(b.high for b in self._bars)
        return QRectF(-1, lo, len(self._bars) + 1, hi - lo)


class BlockItem(pg.GraphicsObject):
    """Filled price blocks from open→close (no wick) — Renko / Range / Line-break bricks."""

    def __init__(self):
        super().__init__()
        self._bars: list = []
        self._picture = QtGui.QPicture()

    def set_bars(self, bars):
        self._bars = bars
        self._generate()
        self.informViewBoundsChanged()
        self.update()

    def _generate(self):
        self._picture = QtGui.QPicture()
        if not self._bars:
            return
        p = QtGui.QPainter(self._picture)
        w = 0.8
        for i, b in enumerate(self._bars):
            up = b.close >= b.open
            color = QtGui.QColor(theme.CANDLE_UP if up else theme.CANDLE_DOWN)
            p.setPen(pg.mkPen(color))
            p.setBrush(pg.mkBrush(color))
            top, bottom = max(b.open, b.close), min(b.open, b.close)
            h = top - bottom
            if h <= 0:
                p.drawLine(pg.Point(i - w / 2, bottom), pg.Point(i + w / 2, bottom))
            else:
                p.drawRect(QRectF(i - w / 2, bottom, w, h))
        p.end()

    def paint(self, painter, *_):
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self):
        if not self._bars:
            return QRectF(0, 0, 1, 1)
        lo = min(b.low for b in self._bars)
        hi = max(b.high for b in self._bars)
        return QRectF(-1, lo, len(self._bars) + 1, hi - lo)


class KagiItem(pg.GraphicsObject):
    """Kagi line: vertical price moves + horizontal turn connectors, thick (yang) / thin (yin)."""

    def __init__(self):
        super().__init__()
        self._res = None
        self._picture = QtGui.QPicture()

    def set_kagi(self, res):
        self._res = res
        self._generate()
        self.informViewBoundsChanged()
        self.update()

    def _generate(self):
        self._picture = QtGui.QPicture()
        res = self._res
        if not res or len(res.prices) < 2:
            return
        p = QtGui.QPainter(self._picture)
        prices = res.prices
        for i in range(1, len(prices)):
            thick = res.thick[i - 1] if i - 1 < len(res.thick) else True
            pen = pg.mkPen(theme.CANDLE_UP if thick else theme.CANDLE_DOWN, width=2.6 if thick else 1.2)
            p.setPen(pen)
            # horizontal connector at the previous level, then the vertical move to the new level
            p.drawLine(pg.Point(i - 1, prices[i - 1]), pg.Point(i, prices[i - 1]))
            p.drawLine(pg.Point(i, prices[i - 1]), pg.Point(i, prices[i]))
        p.end()

    def paint(self, painter, *_):
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self):
        res = self._res
        if not res or not res.prices:
            return QRectF(0, 0, 1, 1)
        lo, hi = min(res.prices), max(res.prices)
        return QRectF(-1, lo, len(res.prices) + 1, (hi - lo) or 1)


class PnFItem(pg.GraphicsObject):
    """Point & Figure: columns of X's (up) and O's (down), one glyph per box."""

    def __init__(self):
        super().__init__()
        self._res = None
        self._picture = QtGui.QPicture()

    def set_pnf(self, res):
        self._res = res
        self._generate()
        self.informViewBoundsChanged()
        self.update()

    def _generate(self):
        self._picture = QtGui.QPicture()
        res = self._res
        if not res or not res.columns:
            return
        box = res.box or 1.0
        p = QtGui.QPainter(self._picture)
        m = 0.36  # glyph half-extent in x
        for ci, col in enumerate(res.columns):
            color = QtGui.QColor(theme.CANDLE_UP if col.up else theme.CANDLE_DOWN)
            p.setPen(pg.mkPen(color, width=1.5))
            p.setBrush(QtCore.Qt.NoBrush)
            n = max(1, int(round((col.top - col.bottom) / box)) + 1)
            for k in range(n):
                y0 = col.bottom + k * box
                y1 = y0 + box
                if col.up:  # X = the two diagonals of the box
                    p.drawLine(pg.Point(ci - m, y0), pg.Point(ci + m, y1))
                    p.drawLine(pg.Point(ci - m, y1), pg.Point(ci + m, y0))
                else:       # O = an ellipse in the box
                    p.drawEllipse(QRectF(ci - m, y0, 2 * m, box))
        p.end()

    def paint(self, painter, *_):
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self):
        res = self._res
        if not res or not res.columns:
            return QRectF(0, 0, 1, 1)
        lo = min(c.bottom for c in res.columns)
        hi = max(c.top for c in res.columns) + (res.box or 1.0)
        return QRectF(-1, lo, len(res.columns) + 1, (hi - lo) or 1)
