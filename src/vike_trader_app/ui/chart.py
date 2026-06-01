"""pyqtgraph charts: a candlestick price chart with trade markers + indicator
overlays, plus an equity curve. Both plot by **bar index** and support progressive
reveal (`show_upto`) so the replay hides future bars like MT5's visual tester.
"""

from datetime import datetime, timezone

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QRectF

from . import theme
from .chartdata import (
    axis_time_label,
    bar_spacing,
    fmt_price,
    follow_window,
    ts_to_x,
    x_to_ts,
    y_bounds,
)

_UP = theme.CANDLE_UP
_DOWN = theme.CANDLE_DOWN
_ENTRY = theme.UP
_EXIT = theme.DOWN
_OVERLAY_COLORS = [theme.FAST, theme.SLOW, "#26c6da", "#66bb6a", "#ec407a"]
_GRID = 0.5  # grid alpha (scales the BORDER tick pen) — subtle but visible, like TradingView
# TradingView-style range selector: (label, days of history to zoom the view to)
_RANGES = [("1D", 1), ("5D", 5), ("1M", 30), ("3M", 90), ("6M", 180), ("1Y", 365), ("5Y", 1825)]
# Timeframe dropdown: (section, [(label, interval)]) — intervals our data sources support.
_TIMEFRAMES = [
    ("Minutes", [("1m", "1m"), ("3m", "3m"), ("5m", "5m"), ("15m", "15m"), ("30m", "30m")]),
    ("Hours", [("1h", "1h"), ("2h", "2h"), ("4h", "4h")]),
    ("Days", [("1D", "1d"), ("1W", "1w")]),
]


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
    """Bottom axis: ticks placed on round wall-clock boundaries (like TradingView),
    mapped from the chart's bar-index x back to each bar's timestamp."""

    _STEPS_MS = [60_000, 120_000, 300_000, 900_000, 1_800_000, 3_600_000, 7_200_000,
                 14_400_000, 21_600_000, 43_200_000, 86_400_000, 172_800_000, 604_800_000]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bars = []

    def set_bars(self, bars):
        self._bars = bars
        self.picture = None
        self.update()

    def tickValues(self, minVal, maxVal, size):
        bars = self._bars
        sp = bar_spacing(bars)
        if len(bars) < 2 or sp <= 0:
            return super().tickValues(minVal, maxVal, size)
        t0, t1 = x_to_ts(bars, minVal), x_to_ts(bars, maxVal)
        if t1 <= t0:
            return super().tickValues(minVal, maxVal, size)
        target = max(2, int(size / 65))  # ~one gridline per 65 px — matches TradingView's vertical grid
        raw = (t1 - t0) / target
        step = next((s for s in self._STEPS_MS if s >= raw), self._STEPS_MS[-1])
        first = (t0 // step) * step
        if first < t0:
            first += step
        ticks, t = [], first
        while t <= t1 and len(ticks) < 60:
            ticks.append(ts_to_x(bars, t))
            t += step
        return [(step / sp, ticks)]

    def tickStrings(self, values, scale, spacing):
        return [axis_time_label(self._bars, v) for v in values]


class PriceAxis(pg.AxisItem):
    """Right-hand price axis with thousands separators and tick-spacing-derived decimals
    (e.g. ``74,600.00`` for BTC, ``1.1650`` for forex) — the TradingView/TradeLocker look."""

    def tickValues(self, minVal, maxVal, size):
        # One evenly-spaced gridline level at a "nice" step (1/2/2.5/5 × 10^k), targeting
        # ~one line per 55 px — TradingView's grid density. (pyqtgraph's default emits a
        # too-coarse major level plus dense minor lines; we want exactly one tidy level.)
        import math

        span = abs(maxVal - minVal)
        if span <= 0 or size <= 0:
            return super().tickValues(minVal, maxVal, size)[:1]
        target = max(2, int(size / 40))  # ~one gridline per 40 px — TradingView's dense, faint grid
        raw = span / target
        mag = 10.0 ** math.floor(math.log10(raw))
        step = next((m * mag for m in (1, 2, 2.5, 5) if raw <= m * mag), 10 * mag)
        first = math.ceil(minVal / step) * step
        ticks, v = [], first
        while v <= maxVal and len(ticks) < 500:
            ticks.append(v)
            v += step
        return [(step, ticks)]

    def tickStrings(self, values, scale, spacing):
        import math

        sp = abs(spacing * scale)
        dec = 2 if sp <= 0 else min(8, max(2, int(math.ceil(-math.log10(sp)))))
        return [f"{v * scale:,.{dec}f}" for v in values]


# TradeStation-style trade markers: buy = blue ▲ below the bar, sell = red ▼ above,
# exit = white arrow above/below (opposite the entry), + a dotted entry→exit connector.
_BUY = theme.BLUE
_SELL = theme.DOWN
_EXIT_C = "#ffffff"
_MARKER_SIZE = 22  # arrow size in px (TradeStation-style prominence)

# Indicator categories that overlay on the PRICE scale (look correct on the candles).
# Oscillators (momentum/volume/etc.) need a separate sub-pane — a later step.
_OVERLAY_CATEGORIES = ("overlap", "price")

# Short codes shown verbatim in upper-case; longer codes get Title Case (e.g. "alligator").
_INDICATOR_NAMES = {
    "ema": "Exponential Moving Average", "sma": "Simple Moving Average",
    "wma": "Weighted Moving Average", "hma": "Hull Moving Average",
    "dema": "Double EMA", "tema": "Triple EMA", "trima": "Triangular MA",
    "vwma": "Volume-Weighted MA", "zlema": "Zero-Lag EMA", "smma": "Smoothed MA",
    "alma": "Arnaud Legoux MA", "t3": "T3 Moving Average", "mcginley": "McGinley Dynamic",
    "gmma": "Guppy Multiple MA", "psar": "Parabolic SAR", "supertrend": "Supertrend",
    "ichimoku": "Ichimoku Cloud", "envelopes": "Envelopes", "alligator": "Alligator",
    "midpoint": "Midpoint", "midprice": "Mid Price", "avgprice": "Average Price",
    "medprice": "Median Price",
}


def _pretty_indicator(code: str) -> str:
    """Human-readable indicator label for the picker (TradingView-style)."""
    if code in _INDICATOR_NAMES:
        return _INDICATOR_NAMES[code]
    return code.upper() if len(code) <= 5 else code.replace("_", " ").title()


class _IndicatorPicker(QtWidgets.QDialog):
    """Searchable, polished list of price-overlay indicators (TradingView/TradeLocker-style)."""

    chosen = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Indicators")
        self.resize(360, 480)
        self.setStyleSheet(
            f"QDialog{{background:{theme.PANEL};}}"
            f"QLineEdit{{background:{theme.PANEL2};border:1px solid {theme.BORDER};"
            f"border-radius:8px;padding:8px 12px;color:{theme.TEXT};font-size:14px;}}"
            f"QLineEdit:focus{{border:1px solid {theme.ACCENT};}}"
            f"QListWidget{{background:transparent;border:none;outline:none;font-size:14px;}}"
            f"QListWidget::item{{color:{theme.TEXT};padding:9px 8px;border-radius:6px;}}"
            f"QListWidget::item:hover{{background:{theme.PANEL2};}}"
            f"QListWidget::item:selected{{background:{theme.RAISE};color:{theme.TEXT};}}"
        )
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(10)
        self._search = QtWidgets.QLineEdit()
        self._search.setPlaceholderText("Search")
        self._search.setClearButtonEnabled(True)
        v.addWidget(self._search)
        hdr = QtWidgets.QLabel("SCRIPT NAME")
        hdr.setStyleSheet(
            f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;"
        )
        v.addWidget(hdr)
        self._list = QtWidgets.QListWidget()
        v.addWidget(self._list, 1)

        import vike_trader_app.core.indicators  # noqa: F401 - populate the REGISTRY
        from vike_trader_app.core.indicators import base as _base

        specs = [s for s in _base.list_indicators() if s.category in _OVERLAY_CATEGORIES]
        for s in sorted(specs, key=lambda x: _pretty_indicator(x.name)):
            item = QtWidgets.QListWidgetItem(_pretty_indicator(s.name))
            item.setData(QtCore.Qt.UserRole, s.name)
            self._list.addItem(item)

        self._search.textChanged.connect(self._filter)
        self._list.itemActivated.connect(self._activate)
        self._list.itemDoubleClicked.connect(self._activate)

    def _filter(self, text):
        t = text.strip().lower()
        for i in range(self._list.count()):
            it = self._list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _activate(self, item):
        self.chosen.emit(item.data(QtCore.Qt.UserRole))
        self.accept()


class PriceChart(pg.PlotWidget):
    """Candles + TradeStation-style trade markers + indicator overlays + a replay cursor,
    with TradingView-style chrome: time axis, mouse crosshair, OHLC legend header, a
    last-price line+badge, and vertical autoscale that fits the visible candles."""

    intervalChosen = QtCore.Signal(str)  # emitted by the timeframe dropdown (e.g. "5m")

    def __init__(self):
        axis = TimeAxis(orientation="bottom")
        super().__init__(axisItems={"bottom": axis, "right": PriceAxis(orientation="right")})
        self._time_axis = axis
        self.setBackground(theme.CHART_BG)
        # Price scale on the RIGHT (TradingView / Lightweight-Charts convention).
        self.showAxis("right")
        self.hideAxis("left")
        self.getAxis("right").setTextPen(theme.TEXT3)
        self.getAxis("bottom").setTextPen(theme.TEXT3)
        # TradingView look: NO hard spine line by the labels — the axis pen is transparent,
        # and the grid is drawn via the (visible) tick pen, so only labels + gridlines show.
        _transparent = pg.mkPen(QtGui.QColor(0, 0, 0, 0))
        for _ax in ("right", "bottom"):
            self.getAxis(_ax).setPen(_transparent)          # no spine
            self.getAxis(_ax).setTickPen(pg.mkPen(theme.BORDER))  # gridline colour
            self.getAxis(_ax).setStyle(tickLength=0)
        self.showGrid(x=True, y=True, alpha=_GRID)
        self.hideButtons()  # hide pyqtgraph's built-in auto-range "A" button (we have our own "Auto")
        self.addLegend(offset=(10, 30), labelTextColor=theme.TEXT2)

        self._bars = []
        self._window = 300  # default candles shown; user can mouse-zoom out
        self._follow = True  # keep the replay cursor in view
        self._yauto = True   # vertical autoscale to the visible candles (TradingView default)
        self._fitting = False  # guard against autoscale re-entrancy
        self._title = ""     # "SYMBOL · interval" prefix for the OHLC header
        self._markers = []   # [{x, price, below, symbol, color, label}] built from trades
        self._marker_labels = []  # TextItem per marker ("Buy"/"Sell"), TradeStation-style
        self._conn = []      # [(entry_x, entry_price, exit_x, exit_price)] dotted connectors
        self._ts_index = {}  # bar timestamp -> index (for trade-row -> chart focus)
        self._overlays = {}  # label -> full series (aligned to bars)
        self._overlay_curves = {}  # label -> PlotDataItem

        self._candles = CandlestickItem([])
        self.addItem(self._candles)
        # dashed entry->exit connectors (under the candles); markers on top.
        # TradeStation/TradingView use a few long dashes, not many tiny dots. Light grey +
        # slightly thicker so the dashes read clearly against the candles.
        _conn_pen = pg.mkPen(theme.TEXT2, width=1.4)
        _conn_pen.setDashPattern([5, 3])  # 5px dash, 3px gap (pen-width units)
        self._conn_curve = self.plot([], [], pen=_conn_pen, connect="finite")
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

        # ---- chart top toolbar: one aligned row (TradingView-style) ----
        # LEFT: timeframe selector | Indicators | OHLC legend.  RIGHT (far): range selector.
        self._top_bar = QtWidgets.QWidget(self)
        # Transparent overlay (TradingView-style): the toolbar floats over the chart so the
        # grid + candles read through it, instead of a solid black band masking that strip.
        # Cascades to the plain child containers/dividers; the buttons/labels keep their own
        # styles (their non-hover background is already transparent).
        self._top_bar.setStyleSheet("QWidget{background:transparent;}")
        _tb = QtWidgets.QHBoxLayout(self._top_bar)
        _tb.setContentsMargins(8, 0, 8, 0)
        _tb.setSpacing(8)
        _btn_qss = (
            f"QPushButton{{color:{theme.TEXT2};background:transparent;border:none;"
            f"padding:2px 9px;font-size:14px;font-weight:400;border-radius:3px;}}"
            f"QPushButton:hover{{color:{theme.TEXT};background:{theme.PANEL};}}"
            f"QPushButton::menu-indicator{{width:0px;}}"
        )

        def _divider():
            ln = QtWidgets.QFrame(self._top_bar)
            ln.setFrameShape(QtWidgets.QFrame.VLine)
            ln.setFixedHeight(15)
            ln.setStyleSheet(f"color:{theme.BORDER};")
            return ln

        # timeframe selector (grouped dropdown) -> emits intervalChosen
        self._tf_btn = QtWidgets.QPushButton("1m", self._top_bar)
        self._tf_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._tf_btn.setStyleSheet(_btn_qss)
        _tf_menu = QtWidgets.QMenu(self._tf_btn)
        for _sec, _items in _TIMEFRAMES:
            _tf_menu.addSection(_sec)
            for _lbl, _iv in _items:
                _tf_menu.addAction(_lbl, lambda iv=_iv: self.intervalChosen.emit(iv))
        self._tf_btn.setMenu(_tf_menu)
        _tb.addWidget(self._tf_btn)
        _tb.addWidget(_divider())

        # indicators (searchable catalog -> overlay)
        self._ind_btn = QtWidgets.QPushButton("ƒx Indicators", self._top_bar)
        self._ind_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._ind_btn.setStyleSheet(_btn_qss)
        self._ind_btn.clicked.connect(self._open_indicator_picker)
        _tb.addWidget(self._ind_btn)
        _tb.addWidget(_divider())

        # OHLC legend
        self._ohlc_label = QtWidgets.QLabel(self._top_bar)
        self._ohlc_label.setTextFormat(QtCore.Qt.RichText)
        self._ohlc_label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        # vertically centre the rich text so it sits on the same line as the buttons (a
        # rich-text QLabel otherwise rode high relative to the timeframe/Indicators row).
        self._ohlc_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._ohlc_label.setStyleSheet(
            f"color:{theme.TEXT2};font-family:{theme.FONT_MONO};font-size:14px;"
            f"font-weight:400;background:transparent;"
        )
        _tb.addWidget(self._ohlc_label, 0, QtCore.Qt.AlignVCenter)

        _tb.addStretch(1)  # push the range selector to the FAR right

        # range selector (tight) -> far top-right
        _range_w = QtWidgets.QWidget(self._top_bar)
        _rb = QtWidgets.QHBoxLayout(_range_w)
        _rb.setContentsMargins(0, 0, 0, 0)
        _rb.setSpacing(0)
        _range_qss = (
            f"QPushButton{{color:{theme.TEXT3};background:transparent;border:none;"
            f"padding:1px 6px;font-size:14px;font-weight:400;border-radius:3px;}}"
            f"QPushButton:hover{{color:{theme.TEXT};background:{theme.PANEL};}}"
        )
        for _label, _days in _RANGES:
            _b = QtWidgets.QPushButton(_label, _range_w)
            _b.setCursor(QtCore.Qt.PointingHandCursor)
            _b.setStyleSheet(_range_qss)
            _b.clicked.connect(lambda _checked=False, d=_days: self.set_visible_range(d))
            _rb.addWidget(_b)
        _tb.addWidget(_range_w)
        self._top_bar.move(0, 4)

        # crosshair axis tag boxes — hovered price on the right axis, time on the bottom axis
        _tag_qss = (f"color:#fff;background:{theme.RAISE};border-radius:2px;padding:0 4px;"
                    f"font-family:{theme.FONT_MONO};font-size:10px;")
        self._cx_price_tag = QtWidgets.QLabel(self)
        self._cx_time_tag = QtWidgets.QLabel(self)
        for _tag in (self._cx_price_tag, self._cx_time_tag):
            _tag.setStyleSheet(_tag_qss)
            _tag.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            _tag.hide()

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
        for lbl in self._marker_labels:
            self.removeItem(lbl)
        self._marker_labels = []
        for t in trades:
            ei = ts_index.get(t.entry_ts)
            if ei is None:
                continue
            long = getattr(t, "size", 1) >= 0
            if long:  # long entry = Buy: blue ▲ below the bar
                self._add_marker(ei, t.entry_price, below=True, symbol="arrow_up",
                                 color=_BUY, text="Buy")
            else:     # short entry = Sell: red ▼ above the bar
                self._add_marker(ei, t.entry_price, below=False, symbol="arrow_down",
                                 color=_SELL, text="Sell")
            xi = ts_index.get(t.exit_ts)
            if xi is not None:  # exit = white arrow, opposite side, labelled by action
                if long:  # long exit = Sell (above)
                    self._add_marker(xi, t.exit_price, below=False, symbol="arrow_down",
                                     color=_EXIT_C, text="Sell")
                else:     # short exit = Buy (below)
                    self._add_marker(xi, t.exit_price, below=True, symbol="arrow_up",
                                     color=_EXIT_C, text="Buy")
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

    # --- indicators (TradingView-style: pick from the catalog, overlay on the chart) ---
    def _open_indicator_picker(self):
        dlg = _IndicatorPicker(self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dlg.chosen.connect(self.add_indicator)
        dlg.exec()

    def add_indicator(self, name: str):
        """Compute indicator ``name`` over the loaded bars and add it as a chart overlay."""
        if not self._bars:
            return
        from vike_trader_app.core.indicators import base as _base

        data = {
            "open": [b.open for b in self._bars],
            "high": [b.high for b in self._bars],
            "low": [b.low for b in self._bars],
            "close": [b.close for b in self._bars],
            "volume": [b.volume for b in self._bars],
        }
        try:
            spec = _base.get(name)
            result = _base.compute(name, data)
        except Exception:  # noqa: BLE001 - bad inputs / unknown indicator -> ignore
            return

        def _clean(seq):  # nan/inf -> None so the overlay renderer skips warmup gaps
            out = []
            for v in seq:
                out.append(None if v is None or (isinstance(v, float) and v != v) else v)
            return out

        merged = dict(self._overlays)
        if len(spec.outputs) <= 1:
            merged[name] = _clean(result)
        else:  # multi-output (e.g. ichimoku, envelopes) -> one line per named output
            for label, series in zip(spec.outputs, result):
                merged[f"{name}:{label}"] = _clean(series)
        self.set_overlays(merged)

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
        # replay cursor only mid-replay; hidden at the end so there's no persistent
        # vertical line at rest (TradingView has none).
        if index < len(self._bars) - 1:
            self._cursor.setPos(index)
            self._cursor.show()
        else:
            self._cursor.hide()
        if self._follow:
            lo, hi = follow_window(index, len(self._bars), self._window)
            self._fitting = True
            self.setXRange(lo, hi, padding=0.02)
            self._fitting = False
        self._update_last()
        self._autoscale_y()
        self._show_last_ohlc()  # header is pinned to the latest candle (not the hovered bar)

    def _add_marker(self, x, price, *, below, symbol, color, text):
        """Register a trade marker + its bold 'Buy'/'Sell' label (TradeStation style)."""
        lbl = pg.TextItem(text=text, color=color, anchor=(0.5, 0.0 if below else 1.0))
        font = QtGui.QFont()
        font.setPointSize(8)
        font.setBold(True)
        lbl.setFont(font)
        self.addItem(lbl, ignoreBounds=True)
        lbl.hide()
        self._marker_labels.append(lbl)
        self._markers.append({"x": x, "price": price, "below": below, "symbol": symbol,
                              "color": color, "label": lbl})

    def _render_markers(self, index: int, off: float = 0.0):
        """Draw revealed buy/sell/exit arrows + 'Buy'/'Sell' labels + dotted connectors."""
        spots = []
        for m in self._markers:
            lbl = m["label"]
            if m["x"] > index:
                lbl.hide()
                continue
            y = m["price"] - off if m["below"] else m["price"] + off
            spots.append({"pos": (m["x"], y), "symbol": m["symbol"], "size": _MARKER_SIZE,
                          "brush": pg.mkBrush(m["color"]), "pen": None})
            lbl.setPos(m["x"], y - off * 1.15 if m["below"] else y + off * 1.15)
            lbl.show()
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

    def set_visible_range(self, days: float):
        """Zoom the view to the last ``days`` of history (the top-left range selector)."""
        if not self._bars or len(self._bars) < 2:
            return
        sp = bar_spacing(self._bars)  # ms per bar
        if sp <= 0:
            return
        n = max(2, int(days * 86_400_000 / sp))
        last = len(self._bars) - 1
        lo = max(0, last - n)
        self._follow = False  # an explicit range selection should stick
        self._window = min(n, len(self._bars))
        self._fitting = True
        self.setXRange(lo, last + 0.5, padding=0.0)
        self._fitting = False
        self._autoscale_y()

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
        self._last_badge.setText(fmt_price(b.close))
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
        ref = bar.close
        # TradingView legend: the O/H/L/C *letters* are white; the *values* take the
        # candle's up/down colour. The change/percent at the end stays coloured too.
        def _cell(letter, val):
            return (f"<span style='color:{theme.TEXT}'>{letter}</span>"
                    f"<span style='color:{col}'>{fmt_price(val, ref)}</span>")

        body = "&nbsp;&nbsp;".join([_cell("O", bar.open), _cell("H", bar.high),
                                    _cell("L", bar.low), _cell("C", bar.close)])
        if prev_close:
            chg = bar.close - prev_close
            pct = chg / prev_close * 100
            s = "+" if chg >= 0 else ""
            body += (f"&nbsp;&nbsp;<span style='color:{col}'>"
                     f"{s}{fmt_price(chg, ref)} ({s}{pct:.2f}%)</span>")
        prefix = (f"<span style='color:{theme.TEXT}'>{self._title}</span>&nbsp;&nbsp;"
                  if self._title else "")
        self._ohlc_label.setText(prefix + body)
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
            self._cx_price_tag.hide()
            self._cx_time_tag.hide()
            self._show_last_ohlc()
            return
        pt = vb.mapSceneToView(scene_pos)
        self._cx_v.setPos(pt.x())
        self._cx_h.setPos(pt.y())
        self._cx_v.show()
        self._cx_h.show()
        # axis tag boxes (scene coords ≈ widget pixels): price on the right, time on the bottom
        py = int(scene_pos.y())
        self._cx_price_tag.setText(f"{pt.y():,.2f}")
        self._cx_price_tag.adjustSize()
        self._cx_price_tag.move(self.width() - self._cx_price_tag.width() - 1,
                                py - self._cx_price_tag.height() // 2)
        self._cx_price_tag.show()
        dt = datetime.fromtimestamp(x_to_ts(self._bars, pt.x()) / 1000, tz=timezone.utc)
        self._cx_time_tag.setText(dt.strftime("%m-%d %H:%M"))
        self._cx_time_tag.adjustSize()
        self._cx_time_tag.move(int(scene_pos.x()) - self._cx_time_tag.width() // 2,
                               self.height() - self._cx_time_tag.height() - 1)
        self._cx_time_tag.show()
        # NB: the OHLC header is intentionally NOT updated to the hovered bar — it stays
        # pinned to the latest candle (the crosshair still reads price/time off the axes).

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if hasattr(self, "_top_bar"):
            # span the chart width but stop short of the right price-axis labels, so the
            # far-right range selector clears them.
            axis_w = self.getAxis("right").width() if self.getAxis("right").isVisible() else 0
            self._top_bar.setGeometry(0, 4, max(0, self.width() - int(axis_w) - 6), 28)
        if hasattr(self, "_auto_btn"):
            self._auto_btn.adjustSize()
            self._auto_btn.move(
                self.width() - self._auto_btn.width() - 8,
                self.height() - self._auto_btn.height() - 6,
            )

    def set_timeframe(self, interval: str):
        """Update the timeframe selector button label (e.g. '5m')."""
        if hasattr(self, "_tf_btn"):
            self._tf_btn.setText(interval)


class EquityChart(pg.PlotWidget):
    """Equity curve over bar index, with progressive reveal for replay."""

    def __init__(self):
        super().__init__()
        self.setBackground(theme.CHART_BG)
        self.showGrid(x=True, y=True, alpha=_GRID)
        self.hideButtons()  # hide pyqtgraph's built-in auto-range "A" button
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
