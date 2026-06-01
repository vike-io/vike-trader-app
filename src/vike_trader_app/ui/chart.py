"""pyqtgraph charts: a candlestick price chart with trade markers + indicator
overlays, plus an equity curve. Both plot by **bar index** and support progressive
reveal (`show_upto`) so the replay hides future bars like MT5's visual tester.
"""

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QRectF

from . import theme
from .chartdata import axis_time_label, follow_window, ohlc_legend_text, y_bounds

_UP = theme.CANDLE_UP
_DOWN = theme.CANDLE_DOWN
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
            if b.high > b.low:  # wick only when there's a range (skip flat/padding bars)
                painter.drawLine(pg.Point(i, b.low), pg.Point(i, b.high))
            top, bottom = max(b.open, b.close), min(b.open, b.close)
            if top > bottom:
                painter.drawRect(QRectF(i - width / 2, bottom, width, top - bottom))
            else:
                # doji / flat bar: a thin horizontal tick, NEVER a degenerate rect (the old
                # `max(top-bottom, 1e-9)` rect rendered as a full-height "wall" on the chart's
                # extreme price scale when many flat padding bars trail the data).
                painter.drawLine(pg.Point(i - width / 2, bottom), pg.Point(i + width / 2, bottom))
        painter.end()

    def paint(self, painter, *_):
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self):
        if not self._bars:
            return QRectF(0, 0, 1, 1)
        lo = min(b.low for b in self._bars)
        hi = max(b.high for b in self._bars)
        return QRectF(-1, lo, len(self._bars) + 1, hi - lo)


class TimeAxis(pg.AxisItem):
    """Bottom axis that labels integer bar-index ticks with each bar's timestamp."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bars = []

    def set_bars(self, bars):
        self._bars = bars
        self.picture = None
        self.update()

    def tickStrings(self, values, scale, spacing):
        return [axis_time_label(self._bars, v) for v in values]


# TradeStation-style trade markers: buy = blue ▲ below the bar, sell = red ▼ above,
# exit = white arrow above/below (opposite the entry), + a dotted entry→exit connector.
_BUY = theme.BLUE
_SELL = theme.DOWN
_EXIT_C = "#ffffff"


class PriceChart(pg.PlotWidget):
    """Candles + TradeStation-style trade markers + indicator overlays + a replay cursor,
    with TradingView-style chrome: time axis, mouse crosshair, OHLC legend header, a
    last-price line+badge, and vertical autoscale that fits the visible candles."""

    def __init__(self):
        axis = TimeAxis(orientation="bottom")
        super().__init__(axisItems={"bottom": axis})
        self._time_axis = axis
        self.setBackground(theme.BG)
        # Price scale on the RIGHT (TradingView / Lightweight-Charts convention).
        self.showAxis("right")
        self.hideAxis("left")
        self.getAxis("right").setTextPen(theme.TEXT3)
        self.getAxis("bottom").setTextPen(theme.TEXT3)
        self.showGrid(x=True, y=True, alpha=_GRID)
        self.addLegend(offset=(10, 30), labelTextColor=theme.TEXT2)

        self._bars = []
        self._window = 300  # default candles shown; user can mouse-zoom out
        self._follow = True  # keep the replay cursor in view
        self._yauto = True   # vertical autoscale to the visible candles (TradingView default)
        self._fitting = False  # guard against autoscale re-entrancy
        self._title = ""     # "SYMBOL · interval" prefix for the OHLC header
        self._markers = []   # [{x, price, below, symbol, color}] built from trades
        self._conn = []      # [(entry_x, entry_price, exit_x, exit_price)] dotted connectors
        self._ts_index = {}  # bar timestamp -> index (for trade-row -> chart focus)
        self._overlays = {}  # label -> full series (aligned to bars)
        self._overlay_curves = {}  # label -> PlotDataItem

        self._candles = CandlestickItem([])
        self.addItem(self._candles)
        # dotted entry->exit connectors (under the candles); markers on top
        self._conn_curve = self.plot(
            [], [], pen=pg.mkPen(theme.TEXT3, width=1, style=QtCore.Qt.DotLine), connect="finite"
        )
        self._marker_scatter = pg.ScatterPlotItem(pen=None)
        self.addItem(self._marker_scatter)

        # replay cursor (vertical, accent) — distinct from the mouse crosshair
        self._cursor = pg.InfiniteLine(angle=90, pen=pg.mkPen(theme.ACCENT, width=1))
        self.addItem(self._cursor)
        self._cursor.hide()

        # last-price dashed line + badge
        self._last_line = pg.InfiniteLine(
            angle=0, movable=False, pen=pg.mkPen(theme.TEXT3, width=1, style=QtCore.Qt.DashLine)
        )
        self.addItem(self._last_line, ignoreBounds=True)
        self._last_line.hide()
        self._last_badge = pg.TextItem(color="#0e0e11", anchor=(0, 0.5), fill=pg.mkBrush(_UP))
        self.addItem(self._last_badge, ignoreBounds=True)
        self._last_badge.hide()

        # mouse crosshair (dim dashed)
        cx_pen = pg.mkPen(theme.TEXT2, width=1, style=QtCore.Qt.DashLine)
        self._cx_v = pg.InfiniteLine(angle=90, movable=False, pen=cx_pen)
        self._cx_h = pg.InfiniteLine(angle=0, movable=False, pen=cx_pen)
        self.addItem(self._cx_v, ignoreBounds=True)
        self.addItem(self._cx_h, ignoreBounds=True)
        self._cx_v.hide()
        self._cx_h.hide()

        # OHLC legend header (top-left overlay)
        self._ohlc_label = QtWidgets.QLabel(self)
        self._ohlc_label.setTextFormat(QtCore.Qt.RichText)
        self._ohlc_label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self._ohlc_label.setStyleSheet(
            f"color:{theme.TEXT2};font-family:{theme.FONT_MONO};font-size:11px;background:transparent;"
        )
        self._ohlc_label.move(12, 6)

        # "Auto" vertical-scale toggle (bottom-right) — on = fit visible candles
        self._auto_btn = QtWidgets.QPushButton("Auto", self)
        self._auto_btn.setCheckable(True)
        self._auto_btn.setChecked(True)
        self._auto_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._auto_btn.setToolTip("Auto-fit the price scale to the visible candles")
        self._auto_btn.setStyleSheet(
            f"QPushButton{{color:{theme.TEXT3};background:{theme.PANEL};border:1px solid {theme.BORDER};"
            f"border-radius:4px;padding:1px 7px;font-size:10px;}}"
            f"QPushButton:checked{{color:{theme.ACCENT};border-color:{theme.ACCENT};}}"
        )
        self._auto_btn.toggled.connect(self._toggle_autoscale)

        self.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.getViewBox().sigXRangeChanged.connect(lambda *_: self._autoscale_y())

    # --- data ---
    def set_title(self, text: str):
        """Set the 'SYMBOL · interval' prefix shown in the OHLC legend header."""
        self._title = text or ""
        self._show_last_ohlc()

    def set_data(self, bars, trades):
        self._bars = bars
        self._time_axis.set_bars(bars)
        ts_index = {b.ts: i for i, b in enumerate(bars)}
        self._ts_index = ts_index
        self._markers, self._conn = [], []
        for t in trades:
            ei = ts_index.get(t.entry_ts)
            if ei is None:
                continue
            long = getattr(t, "size", 1) >= 0
            if long:  # buy entry: blue ▲ below the bar
                self._markers.append({"x": ei, "price": t.entry_price, "below": True,
                                      "symbol": "arrow_up", "color": _BUY})
            else:     # sell (short) entry: red ▼ above the bar
                self._markers.append({"x": ei, "price": t.entry_price, "below": False,
                                      "symbol": "arrow_down", "color": _SELL})
            xi = ts_index.get(t.exit_ts)
            if xi is not None:  # exit: white, opposite side from the entry
                self._markers.append({"x": xi, "price": t.exit_price, "below": not long,
                                      "symbol": "arrow_up" if not long else "arrow_down",
                                      "color": _EXIT_C})
                self._conn.append((ei, t.entry_price, xi, t.exit_price))
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
        # marker offset proportional to the visible price range, so buy/sell/exit arrows
        # sit clearly off the candles on any instrument (BTC vs a 0.99 forex pair).
        if self._follow:
            lo, hi = follow_window(index, len(self._bars), self._window)
        else:
            (vx0, vx1), _ = self.getViewBox().viewRange()
            lo, hi = max(0, int(vx0)), min(len(self._bars), int(vx1) + 1)
        yb = y_bounds(self._bars, lo, min(hi, index + 1))
        marker_off = (yb[1] - yb[0]) * 0.04 if yb and yb[1] > yb[0] else 0.0
        self._render_markers(index, marker_off)
        for label, curve in self._overlay_curves.items():
            series = self._overlays.get(label, [])
            xs = [i for i in range(min(index + 1, len(series))) if series[i] is not None]
            ys = [series[i] for i in xs]
            curve.setData(xs, ys)
        self._cursor.show()
        self._cursor.setPos(index)
        if self._follow:
            lo, hi = follow_window(index, len(self._bars), self._window)
            self._fitting = True
            self.setXRange(lo, hi, padding=0.02)
            self._fitting = False
        self._update_last()
        self._autoscale_y()
        if not self._cx_v.isVisible():
            self._show_last_ohlc()

    def _render_markers(self, index: int, off: float = 0.0):
        """Draw revealed buy/sell/exit arrows (``off`` below/above the fill) + dotted connectors."""
        spots = []
        for m in self._markers:
            if m["x"] > index:
                continue
            y = m["price"] - off if m["below"] else m["price"] + off
            spots.append({"pos": (m["x"], y), "symbol": m["symbol"], "size": 14,
                          "brush": pg.mkBrush(m["color"]), "pen": None})
        self._marker_scatter.setData(spots)
        xs, ys = [], []
        for ex, ep, xx, xp in self._conn:
            if xx > index:
                continue
            xs += [ex, xx, float("nan")]
            ys += [ep, xp, float("nan")]
        self._conn_curve.setData(xs, ys)

    def focus(self, index: int, span: int = 40):
        """Pan/zoom the price view to centre on bar ``index`` (driven by trade-row clicks)."""
        if not self._bars:
            return
        self._follow = False  # an explicit focus range should stick, not be overridden
        lo = max(0, index - span)
        hi = min(len(self._bars), index + span)
        self._fitting = True
        self.setXRange(lo, hi, padding=0.05)
        self._fitting = False
        self._autoscale_y()
        self._cursor.show()
        self._cursor.setPos(index)

    def focus_ts(self, ts: int, span: int = 40):
        """Focus the bar whose timestamp is ``ts`` (a trade fill), if it's in view."""
        idx = self._ts_index.get(ts)
        if idx is not None:
            self.focus(idx, span)

    # --- TradingView-style chrome ---
    def _autoscale_y(self):
        """Fit the Y range to the candles visible in the current X window (when Auto is on)."""
        if self._fitting or not self._yauto or not self._bars:
            return
        (x0, x1), _ = self.getViewBox().viewRange()
        lo = max(0, int(x0))
        hi = min(len(self._bars), int(x1) + 1)
        yb = y_bounds(self._bars, lo, hi)
        if yb and yb[1] > yb[0]:
            self._fitting = True
            self.setYRange(yb[0], yb[1], padding=0.08)
            self._fitting = False

    def _toggle_autoscale(self, on: bool):
        self._yauto = on
        if on:
            self._autoscale_y()

    def _update_last(self):
        bars = self._candles._bars
        if not bars:
            self._last_line.hide()
            self._last_badge.hide()
            return
        i = len(bars) - 1
        b = bars[i]
        prev = bars[i - 1].close if i > 0 else b.open
        col = _UP if b.close >= prev else _DOWN
        self._last_line.setPen(pg.mkPen(col, width=1, style=QtCore.Qt.DashLine))
        self._last_line.setPos(b.close)
        self._last_line.show()
        self._last_badge.setText(f"{b.close:g}")
        self._last_badge.fill = pg.mkBrush(col)
        self._last_badge.setPos(i, b.close)
        self._last_badge.show()

    def _set_ohlc(self, bar, prev_close=None):
        if bar is None:
            self._ohlc_label.setText(self._title)
            self._ohlc_label.adjustSize()
            return
        up = prev_close is None or bar.close >= prev_close
        col = theme.UP if up else theme.DOWN
        prefix = (f"<span style='color:{theme.TEXT}'>{self._title}</span>&nbsp;&nbsp;"
                  if self._title else "")
        self._ohlc_label.setText(f"{prefix}<span style='color:{col}'>{ohlc_legend_text(bar, prev_close)}</span>")
        self._ohlc_label.adjustSize()

    def _show_last_ohlc(self):
        bars = self._candles._bars
        if not bars:
            self._set_ohlc(None)
            return
        b = bars[-1]
        prev = bars[-2].close if len(bars) > 1 else b.open
        self._set_ohlc(b, prev)

    def _on_mouse_moved(self, scene_pos):
        if not self._bars:
            return
        vb = self.getViewBox()
        if not vb.sceneBoundingRect().contains(scene_pos):
            self._cx_v.hide()
            self._cx_h.hide()
            self._show_last_ohlc()
            return
        pt = vb.mapSceneToView(scene_pos)
        self._cx_v.setPos(pt.x())
        self._cx_h.setPos(pt.y())
        self._cx_v.show()
        self._cx_h.show()
        i = int(round(pt.x()))
        revealed = self._candles._bars
        if 0 <= i < len(revealed):
            prev = revealed[i - 1].close if i > 0 else revealed[i].open
            self._set_ohlc(revealed[i], prev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if hasattr(self, "_auto_btn"):
            self._auto_btn.adjustSize()
            self._auto_btn.move(
                self.width() - self._auto_btn.width() - 8,
                self.height() - self._auto_btn.height() - 6,
            )


class EquityChart(pg.PlotWidget):
    """Equity curve over bar index, with progressive reveal for replay."""

    def __init__(self):
        super().__init__()
        self.setBackground(theme.BG)
        self.showGrid(x=True, y=True, alpha=_GRID)
        self.getAxis("left").setTextPen(theme.TEXT3)
        self.getAxis("bottom").setTextPen(theme.TEXT3)
        self._equity = []
        self._peak = []
        self._curve = self.plot([], [], pen=pg.mkPen(theme.UP, width=2))
        # running-peak line (transparent pen so the path still generates for the fill) + a
        # translucent red fill between it and the equity curve = an "underwater"/drawdown
        # shade. TradingView surfaces this; TradeLocker doesn't.
        self._peak_curve = self.plot([], [], pen=pg.mkPen(0, 0, 0, 0))
        self._dd_fill = pg.FillBetweenItem(
            self._curve, self._peak_curve, brush=pg.mkBrush(248, 81, 73, 55)
        )
        self._dd_fill.setZValue(-1)
        self.addItem(self._dd_fill)
        self._baseline = pg.InfiniteLine(
            angle=0, pen=pg.mkPen(theme.TEXT3, width=1, style=QtCore.Qt.DashLine)
        )
        self.addItem(self._baseline)
        self._baseline.hide()

    def set_data(self, equity_curve):
        self._equity = list(equity_curve)
        peak, m = [], float("-inf")
        for v in self._equity:
            m = v if v > m else m
            peak.append(m)
        self._peak = peak
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
        xs = list(range(n))
        self._curve.setData(xs, self._equity[:n])
        self._peak_curve.setData(xs, self._peak[:n])  # drives the drawdown shade
