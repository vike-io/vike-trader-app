"""pyqtgraph charts: a candlestick price chart with trade markers + indicator
overlays, plus an equity curve. Both plot by **bar index** and support progressive
reveal (`show_upto`) so the replay hides future bars like MT5's visual tester.
"""

from datetime import datetime, timezone

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QRectF

from . import dropdowns, theme
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
_CARD_SHADOW = theme.CARD_MARGIN  # translucent margin around frameless picker cards (room for the shadow)
# TradingView-style range selector: (label, days of history to zoom the view to)
_RANGES = [("1D", 1), ("5D", 5), ("1M", 30), ("3M", 90), ("6M", 180), ("1Y", 365), ("5Y", 1825)]
# Timeframe dropdown: (section, [(label, interval)]) — intervals our data sources support.
_TIMEFRAMES = [
    ("Minutes", [("1m", "1m"), ("3m", "3m"), ("5m", "5m"), ("15m", "15m"), ("30m", "30m")]),
    ("Hours", [("1h", "1h"), ("2h", "2h"), ("4h", "4h")]),
    ("Days", [("1D", "1d"), ("1W", "1w")]),
]
# Line-style picker (Style tab): (label, name) — name persists on _Indicator.styles.
_LINE_STYLES = [("Solid", "solid"), ("Dashed", "dashed"), ("Dotted", "dotted")]
_LINE_WIDTHS = [1, 2, 3, 4]  # line-width picker (px)
# Distinct sentinel for _apply_edit optional args (NOT falsy — an empty list/dict is a real value).
_UNSET = object()


def _pen_style(name):
    """Map a style name (solid/dashed/dotted) to a Qt.PenStyle; unknown -> SolidLine."""
    return {
        "solid": QtCore.Qt.SolidLine,
        "dashed": QtCore.Qt.DashLine,
        "dotted": QtCore.Qt.DotLine,
    }.get(name, QtCore.Qt.SolidLine)


def _all_intervals():
    """The flat, ordered list of every supported interval (single source for both the
    per-interval legend menu / Visibility tab and the 'all ⇒ None' normalization)."""
    return [iv for _sec, items in _TIMEFRAMES for _lbl, iv in items]


def _normalize_intervals(chosen):
    """'all checked ⇒ None' rule: None when every interval is selected, else the set."""
    chosen = set(chosen)
    return None if chosen >= set(_all_intervals()) else chosen


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

# Indicators that plot on the PRICE scale (overlay on the candles). Everything else that
# isn't a `pattern` (bar markers) or `pairs` (needs a 2nd symbol) goes in an oscillator pane.
# Category alone is NOT enough — volatility/volume/statistics each mix price-overlays (bands,
# VWAP, linear-reg) with oscillators (ATR, OBV, z-score), so we classify by name.
_OVERLAY_NAMES = frozenset({
    # overlap (all) — moving averages + bands on the price scale
    "alligator", "alma", "dema", "ema", "envelopes", "gmma", "hma", "ichimoku", "mcginley",
    "midpoint", "midprice", "psar", "sma", "smma", "supertrend", "t3", "tema", "trima",
    "vwma", "wma", "zlema",
    # price transforms
    "avgprice", "medprice", "typprice", "wclprice",
    # structure (price levels / lines / fractals on the candles)
    "pivot_points", "volume_profile_poc", "williams_fractal", "zigzag",
    # volatility bands that ride the price scale
    "bollinger", "donchian", "keltner", "high_low_52w",
    # volume / statistics that ride the price scale
    "vwap", "linearreg", "linearreg_intercept", "tsf", "std_error_bands",
})

# Full descriptive names for the picker's right column (TradingView-style). Candlestick
# patterns aren't listed — they title-case cleanly from their snake_case name.
_INDICATOR_NAMES = {
    # overlap
    "ema": "Exponential Moving Average", "sma": "Simple Moving Average",
    "wma": "Weighted Moving Average", "hma": "Hull Moving Average", "dema": "Double EMA",
    "tema": "Triple EMA", "trima": "Triangular Moving Average", "vwma": "Volume-Weighted MA",
    "zlema": "Zero-Lag EMA", "smma": "Smoothed Moving Average", "alma": "Arnaud Legoux MA",
    "t3": "T3 Moving Average", "mcginley": "McGinley Dynamic", "gmma": "Guppy Multiple MA",
    "psar": "Parabolic SAR", "supertrend": "Supertrend", "ichimoku": "Ichimoku Cloud",
    "envelopes": "Moving Average Envelopes", "alligator": "Williams Alligator",
    "midpoint": "Midpoint", "midprice": "Mid Price",
    # price
    "avgprice": "Average Price", "medprice": "Median Price", "typprice": "Typical Price",
    "wclprice": "Weighted Close Price",
    # momentum
    "ac": "Accelerator Oscillator", "adx": "Average Directional Index", "adxr": "ADX Rating",
    "ao": "Awesome Oscillator", "apo": "Absolute Price Oscillator", "aroon": "Aroon",
    "aroonosc": "Aroon Oscillator", "asi": "Accumulative Swing Index", "bop": "Balance of Power",
    "cci": "Commodity Channel Index", "chande_kroll_stop": "Chande Kroll Stop",
    "cmo": "Chande Momentum Oscillator", "connors_rsi": "Connors RSI", "coppock": "Coppock Curve",
    "dpo": "Detrended Price Oscillator", "elder_ray": "Elder Ray Index", "fisher": "Fisher Transform",
    "kst": "Know Sure Thing", "macd": "MACD", "mom": "Momentum",
    "ppo": "Percentage Price Oscillator", "relative_vigor": "Relative Vigor Index",
    "roc": "Rate of Change", "rocp": "Rate of Change (%)", "rocr": "Rate of Change Ratio",
    "rocr100": "Rate of Change Ratio (100)", "rsi": "Relative Strength Index",
    "smi_ergodic": "SMI Ergodic Indicator", "stochastic": "Stochastic",
    "stochf": "Stochastic Fast", "stochrsi": "Stochastic RSI", "trix": "TRIX",
    "tsi": "True Strength Index", "ultosc": "Ultimate Oscillator", "vortex": "Vortex Indicator",
    "williams_r": "Williams %R",
    # volatility
    "atr": "Average True Range", "bbands_pctb": "Bollinger %B", "bbands_width": "Bollinger Bandwidth",
    "bollinger": "Bollinger Bands", "chop": "Choppiness Index", "donchian": "Donchian Channels",
    "donchian_width": "Donchian Width", "high_low_52w": "52-Week High/Low",
    "hvol": "Historical Volatility", "keltner": "Keltner Channels", "mass": "Mass Index",
    "natr": "Normalized ATR", "relative_volatility": "Relative Volatility Index",
    "stddev": "Standard Deviation", "true_range": "True Range", "ulcer": "Ulcer Index",
    # volume
    "ad": "Accumulation/Distribution", "adosc": "Chaikin A/D Oscillator", "cmf": "Chaikin Money Flow",
    "efi": "Elder Force Index", "eom": "Ease of Movement", "kvo": "Klinger Volume Oscillator",
    "net_volume": "Net Volume", "nvi": "Negative Volume Index", "obv": "On-Balance Volume",
    "pvi": "Positive Volume Index", "pvt": "Price Volume Trend", "volume_osc": "Volume Oscillator",
    "vwap": "VWAP",
    # statistics
    "beta": "Beta", "correl": "Correlation Coefficient", "correl_log": "Log Correlation",
    "kurtosis": "Kurtosis", "linearreg": "Linear Regression", "linearreg_angle": "Linear Reg Angle",
    "linearreg_intercept": "Linear Reg Intercept", "linearreg_slope": "Linear Reg Slope",
    "mad": "Mean Absolute Deviation", "rank_correlation": "Rank Correlation", "skew": "Skewness",
    "std_error": "Standard Error", "std_error_bands": "Standard Error Bands",
    "tsf": "Time Series Forecast", "var": "Variance", "zscore": "Z-Score",
    # structure
    "pivot_points": "Pivot Points", "volume_profile_poc": "Volume Profile POC",
    "williams_fractal": "Williams Fractal", "zigzag": "ZigZag",
    # pairs
    "ratio": "Price Ratio", "spread": "Spread", "spread_zscore": "Spread Z-Score",
}

# Friendly tab labels for the picker, mapped to the registry categories they show.
_PICKER_TABS = [
    ("All", None),
    ("Trend", {"overlap", "price"}),
    ("Momentum", {"momentum"}),
    ("Volatility", {"volatility"}),
    ("Volume", {"volume"}),
    ("Statistics", {"statistics"}),
    ("Structure", {"structure"}),
    ("Pattern", {"pattern"}),
    ("Pairs", {"pairs"}),
]


def _pretty_indicator(code: str) -> str:
    """Full descriptive indicator name for the picker (TradingView-style)."""
    if code in _INDICATOR_NAMES:
        return _INDICATOR_NAMES[code]
    return code.replace("_", " ").title()  # patterns + any long tail read well title-cased


def _indicator_code(name: str) -> str:
    """Short upper-case code for the picker's left column (e.g. 'rsi' -> 'RSI')."""
    return name.upper()


class _IndicatorPicker(dropdowns.PopupCard):
    """TradingView-style indicator picker: a search field + category pill tabs over a
    two-column (short CODE / full NAME) list of the whole 176-indicator registry."""

    chosen = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, object_name="pickerCard", extra_qss=(
            f"QLineEdit{{background:{theme.BG};border:1px solid {theme.BORDER};"
            f"border-radius:10px;padding:9px 12px;color:{theme.TEXT};font-size:14px;}}"
            f"QLineEdit:focus{{border:1px solid {theme.ACCENT};}}"
            f"QPushButton#tab{{background:transparent;border:none;color:{theme.TEXT3};"
            f"padding:6px 13px;border-radius:9px;font-size:13px;font-weight:600;}}"
            f"QPushButton#tab:hover{{color:{theme.TEXT2};}}"
            f"QPushButton#tab:checked{{background:{theme.HOVER};color:{theme.TEXT};}}"
            f"QListWidget#indList{{background:transparent;border:none;outline:none;}}"
            f"QListWidget#indList::item{{border:none;border-bottom:0px;border-radius:8px;"
            f"margin:1px 2px;padding:0;}}"
            f"QListWidget#indList::item:hover{{background:{theme.HOVER};}}"
            f"QListWidget#indList::item:selected{{background:{theme.HOVER};}}"
            f"QScrollBar:vertical{{background:transparent;width:9px;margin:4px 2px;}}"
            f"QScrollBar::handle:vertical{{background:{theme.BORDER};border-radius:4px;min-height:30px;}}"
            f"QScrollBar::add-line,QScrollBar::sub-line{{height:0;}}"
        ))
        self.setWindowTitle("Indicators")
        self.resize_card(470, 600)
        card = self.card
        outer = QtWidgets.QVBoxLayout(card)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(11)

        self._search = QtWidgets.QLineEdit()
        self._search.setPlaceholderText("Search for indicators")
        self._search.setClearButtonEnabled(True)
        self._search.addAction(self._search_icon(), QtWidgets.QLineEdit.LeadingPosition)
        outer.addWidget(self._search)

        # category pill tabs (exclusive)
        tabrow = QtWidgets.QHBoxLayout()
        tabrow.setSpacing(2)
        tabrow.setContentsMargins(0, 0, 0, 0)
        self._tabs = QtWidgets.QButtonGroup(self)
        self._tabs.setExclusive(True)
        for i, (label, _cats) in enumerate(_PICKER_TABS):
            b = QtWidgets.QPushButton(label)
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            self._tabs.addButton(b, i)
            tabrow.addWidget(b)
        tabrow.addStretch(1)
        outer.addLayout(tabrow)

        self._list = QtWidgets.QListWidget()
        self._list.setObjectName("indList")  # ID selector beats the global QListWidget::item border
        self._list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        outer.addWidget(self._list, 1)

        import vike_trader_app.core.indicators  # noqa: F401 - populate the REGISTRY
        from vike_trader_app.core.indicators import base as _base

        self._rows = []  # (item, haystack, category) for filtering
        for s in sorted(_base.list_indicators(), key=lambda x: x.name):
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.UserRole, s.name)
            row = QtWidgets.QWidget()
            row.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)  # clicks -> the list item
            row.setStyleSheet("background:transparent;")  # else the global QWidget bg darkens rows
            rl = QtWidgets.QHBoxLayout(row)
            rl.setContentsMargins(12, 9, 12, 9)
            rl.setSpacing(14)
            code = QtWidgets.QLabel(_indicator_code(s.name))
            code.setFixedWidth(150)
            code.setStyleSheet(
                f"color:{theme.TEXT};font-weight:600;font-size:15px;background:transparent;"
            )
            name = QtWidgets.QLabel(_pretty_indicator(s.name))
            name.setStyleSheet(f"color:{theme.TEXT3};font-size:13px;background:transparent;")
            rl.addWidget(code)
            rl.addWidget(name, 1)
            item.setSizeHint(row.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
            self._rows.append((item, f"{s.name} {_pretty_indicator(s.name)}".lower(), s.category))

        self._cats = None  # active tab's category set; None = All
        self._tabs.idClicked.connect(self._on_tab)
        self._tabs.button(0).setChecked(True)
        self._search.textChanged.connect(lambda *_: self._apply())
        self._list.itemClicked.connect(self._activate)    # single click -> add + close
        self._list.itemActivated.connect(self._activate)  # Enter / keyboard
        self._search.setFocus()

    def event(self, e):  # noqa: A003 - Qt override; close when focus leaves (click outside)
        if e.type() == QtCore.QEvent.WindowDeactivate:
            self.close()
        return super().event(e)

    @staticmethod
    def _search_icon() -> QtGui.QIcon:
        # 2x pixmap for a crisp, friendly magnifier (round caps, brighter than the placeholder)
        s, dpr = 36, 2
        pm = QtGui.QPixmap(s * dpr, s * dpr)
        pm.setDevicePixelRatio(dpr)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        pen = QtGui.QPen(QtGui.QColor(theme.TEXT2))
        pen.setWidthF(2.6)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.drawEllipse(QtCore.QRectF(8.5, 8.5, 13.5, 13.5))
        p.drawLine(QtCore.QPointF(21.5, 21.5), QtCore.QPointF(27.5, 27.5))
        p.end()
        return QtGui.QIcon(pm)

    def _on_tab(self, idx: int):
        self._cats = _PICKER_TABS[idx][1]
        self._apply()

    def _apply(self):
        t = self._search.text().strip().lower()
        for item, hay, cat in self._rows:
            ok = (self._cats is None or cat in self._cats) and (not t or t in hay)
            item.setHidden(not ok)

    def _activate(self, item):
        self.chosen.emit(item.data(QtCore.Qt.UserRole))
        self.accept()


class _Indicator:
    """One active indicator instance on a chart. Stores everything needed to recompute and
    re-render it after a parameter/style edit, a move between panes, or a hide/show toggle."""

    _seq = 0

    def __init__(self, name, spec, params, kind):
        _Indicator._seq += 1
        self.uid = _Indicator._seq
        self.name = name
        self.spec = spec
        self.params = dict(params)       # current input values (param name -> value)
        self.kind = kind                 # 'overlay' | 'oscillator' | 'pattern' | 'pairs'
        self.visible = True              # user hide/show toggle
        self.intervals = None            # None = all timeframes; else the set it's visible on
        self.shown = True                # effective visibility (visible AND interval-allowed)
        self.own_scale = False           # overlay pinned to its own (independent) right scale
        self.benchmark = None            # aligned 2nd-symbol closes (pairs only)
        self.colors = list(_OVERLAY_COLORS[: max(1, len(spec.outputs))])  # per-output colour
        self.widths = [1] * max(1, len(spec.outputs))    # per-output line width (px)
        self.styles = ["solid"] * max(1, len(spec.outputs))  # per-output line style name
        self.series = {}                 # computed: output label -> full series
        # render handles (set when rendered):
        self.curves = {}                 # overlay/oscillator: output label -> PlotDataItem
        self.pane = None                 # OscillatorPane (oscillator/pairs)
        self.scatter = None              # pattern marker ScatterPlotItem

    @staticmethod
    def spec_defaults(spec):
        """Single source of truth for the Defaults button and add_indicator seeding:
        (params, colors, widths, styles) at the registry's defaults."""
        n = max(1, len(spec.outputs))
        params = {p.name: p.default for p in spec.params}
        colors = list(_OVERLAY_COLORS[:n])
        widths = [1] * n
        styles = ["solid"] * n
        return params, colors, widths, styles

    @property
    def label(self) -> str:
        """Legend label, TradingView-style: 'RSI 14' (name + non-default param values)."""
        base = _indicator_code(self.name)
        vals = [str(self.params[p.name]) for p in self.spec.params]
        return f"{base} {' '.join(vals)}".strip() if vals else base


class _IndicatorSettings(dropdowns.PopupCard):
    """TradingView-style settings: **Inputs** (registry params), **Style** (per-output colour +
    line width + line style), and **Visibility** (per-interval) tabs. Emits
    ``applied(params, colors, widths, styles, intervals)`` on Ok."""

    applied = QtCore.Signal(dict, list, list, list, object)

    def __init__(self, ind: "_Indicator", parent=None):
        super().__init__(parent, object_name="setCard", extra_qss=(
            f"QLabel{{color:{theme.TEXT2};background:transparent;}}"
            f"QTabBar::tab{{background:transparent;color:{theme.TEXT3};padding:6px 14px;"
            f"border:none;border-bottom:2px solid transparent;font-size:13px;font-weight:600;}}"
            f"QTabBar::tab:selected{{color:{theme.TEXT};border-bottom:2px solid {theme.ACCENT};}}"
            f"QTabWidget::pane{{border:none;}}"
            f"QSpinBox,QDoubleSpinBox,QComboBox{{background:{theme.BG};color:{theme.TEXT};"
            f"border:1px solid {theme.BORDER};border-radius:6px;padding:4px 8px;min-width:90px;}}"
            f"QPushButton{{background:{theme.BG};color:{theme.TEXT};border:1px solid {theme.BORDER};"
            f"border-radius:7px;padding:6px 14px;}}"
            f"QPushButton#ok{{background:{theme.ACCENT};color:{theme.ON_ACCENT};border:none;font-size:14px;font-weight:700;}}"
        ))
        self._spec = ind.spec
        self._ind = ind
        card = self.card
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)

        head = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(_pretty_indicator(ind.name))
        title.setStyleSheet(f"color:{theme.TEXT};font-size:15px;font-weight:700;background:transparent;")
        close = QtWidgets.QPushButton("✕")
        close.setFlat(True)
        close.setStyleSheet("QPushButton{background:transparent;border:none;color:%s;}" % theme.TEXT3)
        close.clicked.connect(self.reject)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(close)
        v.addLayout(head)

        tabs = QtWidgets.QTabWidget()
        v.addWidget(tabs)

        # --- Inputs tab (one editor per registry Param) ---
        inputs = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(inputs)
        form.setContentsMargins(4, 10, 4, 4)
        form.setSpacing(9)
        self._param_widgets = {}
        for p in self._spec.params:
            if p.type == "int":
                w = QtWidgets.QSpinBox()
                w.setRange(int(p.min if p.min is not None else -10**9),
                           int(p.max if p.max is not None else 10**9))
                w.setSingleStep(int(p.step or 1))
                w.setValue(int(ind.params.get(p.name, p.default)))
            else:
                w = QtWidgets.QDoubleSpinBox()
                w.setDecimals(4)
                w.setRange(float(p.min if p.min is not None else -1e12),
                           float(p.max if p.max is not None else 1e12))
                w.setSingleStep(float(p.step or 0.1))
                w.setValue(float(ind.params.get(p.name, p.default)))
            self._param_widgets[p.name] = w
            form.addRow(p.name.replace("_", " ").title(), w)
        if not self._spec.params:
            form.addRow(QtWidgets.QLabel("This indicator has no inputs."))
        tabs.addTab(inputs, "Inputs")

        # --- Style tab (per output: colour + line width + line style) ---
        style = QtWidgets.QWidget()
        sform = QtWidgets.QFormLayout(style)
        sform.setContentsMargins(4, 10, 4, 4)
        sform.setSpacing(9)
        self._color_btns = []
        self._width_combos = []
        self._style_combos = []
        is_pattern = ind.kind == "pattern"
        widths = getattr(ind, "widths", [1] * len(self._spec.outputs))
        styles = getattr(ind, "styles", ["solid"] * len(self._spec.outputs))
        for i, out in enumerate(self._spec.outputs):
            btn = QtWidgets.QPushButton()
            btn.setFixedSize(46, 22)
            col = ind.colors[i] if i < len(ind.colors) else _OVERLAY_COLORS[i % len(_OVERLAY_COLORS)]
            self._set_btn_color(btn, col)
            btn.clicked.connect(lambda _c=False, b=btn: self._pick_color(b))
            self._color_btns.append(btn)

            wcb = QtWidgets.QComboBox()
            for w in _LINE_WIDTHS:
                wcb.addItem(f"{w}px", w)  # userData = int width
            cur_w = widths[i] if i < len(widths) else 1
            wcb.setCurrentIndex(max(0, _LINE_WIDTHS.index(cur_w) if cur_w in _LINE_WIDTHS else 0))
            self._width_combos.append(wcb)

            scb = QtWidgets.QComboBox()
            for lbl, nm in _LINE_STYLES:
                scb.addItem(lbl, nm)      # userData = str style name
            cur_s = styles[i] if i < len(styles) else "solid"
            names = [nm for _lbl, nm in _LINE_STYLES]
            scb.setCurrentIndex(names.index(cur_s) if cur_s in names else 0)
            self._style_combos.append(scb)

            roww = QtWidgets.QWidget()
            rowl = QtWidgets.QHBoxLayout(roww)
            rowl.setContentsMargins(0, 0, 0, 0)
            rowl.setSpacing(6)
            rowl.addWidget(btn)
            rowl.addWidget(wcb)
            rowl.addWidget(scb)
            if is_pattern:  # markers use brushes, not pens -> no width/style
                wcb.hide()
                scb.hide()
            sform.addRow(out.replace("_", " ").title(), roww)
        tabs.addTab(style, "Style")

        # --- Visibility tab (per-interval checkboxes, grouped by section) ---
        vis = QtWidgets.QWidget()
        vform = QtWidgets.QVBoxLayout(vis)
        vform.setContentsMargins(4, 10, 4, 4)
        vform.setSpacing(4)
        self._iv_checks = {}
        cur_intervals = getattr(ind, "intervals", None)
        for sec, items in _TIMEFRAMES:
            seclbl = QtWidgets.QLabel(sec.upper())
            seclbl.setStyleSheet(
                f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;"
                f"background:transparent;margin-top:6px;"
            )
            vform.addWidget(seclbl)
            for lbl, iv in items:
                cb = QtWidgets.QCheckBox(lbl)
                cb.setStyleSheet(f"color:{theme.TEXT2};background:transparent;")
                cb.setChecked(cur_intervals is None or iv in cur_intervals)
                self._iv_checks[iv] = cb
                vform.addWidget(cb)
        vform.addStretch(1)
        tabs.addTab(vis, "Visibility")

        foot = QtWidgets.QHBoxLayout()
        foot.addStretch(1)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QtWidgets.QPushButton("Ok")
        ok.setObjectName("ok")
        ok.clicked.connect(self._accept)
        foot.addWidget(cancel)
        foot.addWidget(ok)
        v.addLayout(foot)
        self.resize(360, 440)

    def _chosen_intervals(self):
        """Intervals selected in the Visibility tab, normalized (all ⇒ None)."""
        return _normalize_intervals(
            iv for iv, cb in self._iv_checks.items() if cb.isChecked()
        )

    @staticmethod
    def _set_btn_color(btn, color):
        btn.setStyleSheet(f"background:{color};border:1px solid {theme.BORDER};border-radius:5px;")
        btn.setProperty("color_hex", color)

    def _pick_color(self, btn):
        cur = QtGui.QColor(btn.property("color_hex"))
        chosen = QtWidgets.QColorDialog.getColor(cur, self, "Plot colour")
        if chosen.isValid():
            self._set_btn_color(btn, chosen.name())

    def _accept(self):
        params = {}
        for p in self._spec.params:
            params[p.name] = self._param_widgets[p.name].value()
        colors = [b.property("color_hex") for b in self._color_btns]
        widths = [int(c.currentData()) for c in self._width_combos]
        styles = [str(c.currentData()) for c in self._style_combos]
        intervals = self._chosen_intervals()
        self.applied.emit(params, colors, widths, styles, intervals)
        self.accept()


def _mono_tick_font() -> QtGui.QFont:
    """The 12px monospace font used for chart axis tick labels."""
    f = QtGui.QFont(theme.FONT_MONO.split(",")[0].strip('"'))
    f.setPixelSize(12)
    return f


def _eye_icon(open_: bool) -> QtGui.QIcon:
    """A small eye (open) / eye-with-slash (hidden) icon for the legend's hide toggle."""
    s, dpr = 18, 2
    pm = QtGui.QPixmap(s * dpr, s * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor(theme.TEXT3 if open_ else theme.TEXT3))
    pen.setWidthF(1.5)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    path = QtGui.QPainterPath()
    path.moveTo(3, 9)
    path.quadTo(9, 2.5, 15, 9)
    path.quadTo(9, 15.5, 3, 9)
    p.drawPath(path)
    p.drawEllipse(QtCore.QPointF(9, 9), 2.1, 2.1)
    if not open_:
        p.drawLine(QtCore.QPointF(4, 14), QtCore.QPointF(14, 4))
    p.end()
    return QtGui.QIcon(pm)


def _pane_icon(kind: str) -> QtGui.QIcon:
    """Painter-drawn glyph for the pane hover toolbar — `up`/`down`/`max`/`restore`/`del`
    (theme.TEXT3, no image assets), mirroring `_eye_icon`'s pixmap recipe."""
    s, dpr = 18, 2
    pm = QtGui.QPixmap(s * dpr, s * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor(theme.TEXT3))
    pen.setWidthF(1.5)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    if kind in ("up", "down"):
        # chevron arrow
        if kind == "up":
            p.drawLine(QtCore.QPointF(4, 11), QtCore.QPointF(9, 6))
            p.drawLine(QtCore.QPointF(9, 6), QtCore.QPointF(14, 11))
        else:
            p.drawLine(QtCore.QPointF(4, 7), QtCore.QPointF(9, 12))
            p.drawLine(QtCore.QPointF(9, 12), QtCore.QPointF(14, 7))
    elif kind == "max":
        p.drawRect(QtCore.QRectF(4, 4, 10, 10))          # outer frame = maximize
    elif kind == "restore":
        p.drawRect(QtCore.QRectF(4, 6, 8, 8))            # two offset frames = restore
        p.drawLine(QtCore.QPointF(6, 6), QtCore.QPointF(6, 4))
        p.drawLine(QtCore.QPointF(6, 4), QtCore.QPointF(14, 4))
        p.drawLine(QtCore.QPointF(14, 4), QtCore.QPointF(14, 12))
        p.drawLine(QtCore.QPointF(14, 12), QtCore.QPointF(12, 12))
    elif kind == "del":
        p.drawLine(QtCore.QPointF(4, 5), QtCore.QPointF(14, 5))   # trash lid
        p.drawLine(QtCore.QPointF(7, 5), QtCore.QPointF(7, 3))
        p.drawLine(QtCore.QPointF(7, 3), QtCore.QPointF(11, 3))
        p.drawLine(QtCore.QPointF(11, 3), QtCore.QPointF(11, 5))
        path = QtGui.QPainterPath()                              # trash body
        path.moveTo(5, 6)
        path.lineTo(6, 15)
        path.lineTo(12, 15)
        path.lineTo(13, 6)
        p.drawPath(path)
    p.end()
    return QtGui.QIcon(pm)


class _PaneToolbar(QtWidgets.QWidget):
    """A small floating horizontal strip of 4 buttons (move up / move down / maximize-restore /
    delete pane), shown on pane hover at the top-right — TradingView's per-pane toolbar. Styled
    like `_LegendRow._btn` (transparent, autoRaise, TEXT3 -> TEXT on hover). Parented to the pane
    as a child overlay (like `_header`); hidden by default."""

    moveUp = QtCore.Signal()
    moveDown = QtCore.Signal()
    maximizeToggled = QtCore.Signal()
    deletePane = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent;")
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)
        self._up = self._btn(_pane_icon("up"), "Move pane up")
        self._down = self._btn(_pane_icon("down"), "Move pane down")
        self._max = self._btn(_pane_icon("max"), "Maximize pane")
        self._del = self._btn(_pane_icon("del"), "Delete pane")
        self._up.clicked.connect(self.moveUp)
        self._down.clicked.connect(self.moveDown)
        self._max.clicked.connect(self.maximizeToggled)
        self._del.clicked.connect(self.deletePane)
        for b in (self._up, self._down, self._max, self._del):
            h.addWidget(b)
        self.adjustSize()

    def _btn(self, icon: QtGui.QIcon, tip: str) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton(self)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setAutoRaise(True)
        b.setIcon(icon)
        b.setIconSize(QtCore.QSize(15, 15))
        b.setToolTip(tip)
        b.setStyleSheet(
            f"QToolButton{{background:transparent;border:none;color:{theme.TEXT3};padding:0 2px;}}"
            f"QToolButton:hover{{color:{theme.TEXT};}}"
        )
        return b

    def set_can_up(self, on: bool):
        self._up.setEnabled(on)

    def set_can_down(self, on: bool):
        self._down.setEnabled(on)

    def set_maximized(self, on: bool):
        self._max.setIcon(_pane_icon("restore" if on else "max"))
        self._max.setToolTip("Restore pane" if on else "Maximize pane")


class _DragGrip(QtWidgets.QLabel):
    """A small ⠿ handle for drag-to-reorder of an oscillator pane. Emits the cursor's global y
    while dragging so the chart can live-reorder the panes, and a signal on release."""

    dragged = QtCore.Signal(int)   # cursor global y during a drag
    released = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__("⠿", parent)
        self.setCursor(QtCore.Qt.SizeVerCursor)
        self.setToolTip("Drag to reorder pane")
        self.setStyleSheet(f"QLabel{{color:{theme.TEXT3};background:transparent;font-size:13px;}}")
        self._down = False

    def mousePressEvent(self, e):  # noqa: N802 - Qt override
        if e.button() == QtCore.Qt.LeftButton:
            self._down = True
            e.accept()

    def mouseMoveEvent(self, e):  # noqa: N802 - Qt override
        if self._down:
            self.dragged.emit(int(e.globalPosition().y()))
            e.accept()

    def mouseReleaseEvent(self, e):  # noqa: N802 - Qt override
        if self._down:
            self._down = False
            self.released.emit()
            e.accept()


class _LegendRow(QtWidgets.QWidget):
    """One indicator's legend entry: label (+ live value) with a quick eye (hide/show) toggle
    and a ⋯ menu (Settings / Move to / Hide / Remove). Reused on the price pane and in each
    oscillator pane's header. Double-clicking the row opens Settings."""

    editRequested = QtCore.Signal(int)
    removeRequested = QtCore.Signal(int)
    hideToggled = QtCore.Signal(int)
    moveRequested = QtCore.Signal(int, str)    # (uid, target: new/price/up/down/merge_*)
    actionRequested = QtCore.Signal(int, str)  # (uid, action: clone/front/back/forward/backward)

    def __init__(self, ind: "_Indicator", parent=None):
        super().__init__(parent)
        self._uid = ind.uid
        self._ind = ind
        self.setStyleSheet("background:transparent;")
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(5)
        self._name = QtWidgets.QLabel(ind.label)
        self._name.setStyleSheet(
            f"color:{theme.TEXT2};font-size:13px;font-weight:600;background:transparent;"
        )
        self._val = QtWidgets.QLabel("")
        self._val.setStyleSheet(
            f"color:{theme.TEXT3};font-size:13px;font-family:{theme.FONT_MONO};background:transparent;"
        )
        self._eye = self._btn()
        self._eye.setIcon(_eye_icon(ind.visible))
        self._eye.setIconSize(QtCore.QSize(15, 15))
        self._eye.clicked.connect(lambda: self.hideToggled.emit(self._uid))
        self._more = self._btn()
        self._more.setText("⋯")
        self._more.setStyleSheet(self._more.styleSheet() + "QToolButton{font-size:14px;}")
        self._more.clicked.connect(self._open_menu)
        h.addWidget(self._name)
        h.addWidget(self._val)
        h.addWidget(self._eye)
        h.addWidget(self._more)

    def _btn(self) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton(self)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setAutoRaise(True)
        b.setStyleSheet(
            f"QToolButton{{background:transparent;border:none;color:{theme.TEXT3};padding:0 2px;}}"
            f"QToolButton:hover{{color:{theme.TEXT};}}"
        )
        return b

    def mouseDoubleClickEvent(self, e):  # noqa: N802 - Qt override
        self.editRequested.emit(self._uid)

    def set_value(self, text: str):
        self._val.setText(text)

    def refresh(self, ind: "_Indicator"):
        self._ind = ind
        self._name.setText(ind.label)
        self._eye.setIcon(_eye_icon(ind.visible))

    def _open_menu(self):
        uid, kind = self._uid, self._ind.kind
        m = QtWidgets.QMenu(self)
        m.addAction("Settings…", lambda: self.editRequested.emit(uid))
        m.addAction("Clone", lambda: self.actionRequested.emit(uid, "clone"))
        if kind == "overlay":
            vo = m.addMenu("Visual order")
            vo.addAction("Bring to front", lambda: self.actionRequested.emit(uid, "front"))
            vo.addAction("Bring forward", lambda: self.actionRequested.emit(uid, "forward"))
            vo.addAction("Send backward", lambda: self.actionRequested.emit(uid, "backward"))
            vo.addAction("Send to back", lambda: self.actionRequested.emit(uid, "back"))
            ps = m.addMenu("Pin to scale")
            a_price = ps.addAction("Price (shared)", lambda: self.actionRequested.emit(uid, "pin_price"))
            a_own = ps.addAction("Own scale", lambda: self.actionRequested.emit(uid, "pin_own"))
            for a, on in ((a_price, not self._ind.own_scale), (a_own, self._ind.own_scale)):
                a.setCheckable(True)
                a.setChecked(on)
        vis = m.addMenu("Visibility on intervals")  # per-timeframe show/hide
        for _sec, items in _TIMEFRAMES:
            for lbl, iv in items:
                a = vis.addAction(lbl)
                a.setCheckable(True)
                a.setChecked(self._ind.intervals is None or iv in self._ind.intervals)
                a.triggered.connect(lambda _c=False, i=iv: self.actionRequested.emit(uid, f"iv:{i}"))
        move = m.addMenu("Move to")
        move.addAction("New pane below", lambda: self.moveRequested.emit(uid, "new"))
        if kind in ("oscillator", "pairs"):
            move.addAction("Merge into price", lambda: self.moveRequested.emit(uid, "price"))
            move.addAction("Merge with pane above", lambda: self.moveRequested.emit(uid, "merge_above"))
            move.addAction("Merge with pane below", lambda: self.moveRequested.emit(uid, "merge_below"))
            m.addAction("Move pane up", lambda: self.moveRequested.emit(uid, "up"))
            m.addAction("Move pane down", lambda: self.moveRequested.emit(uid, "down"))
        m.addSeparator()
        m.addAction("Hide" if self._ind.visible else "Show",
                    lambda: self.hideToggled.emit(uid))
        m.addAction("Object tree…", lambda: self.actionRequested.emit(uid, "tree"))
        m.addAction("Remove", lambda: self.removeRequested.emit(uid))
        m.exec(self._more.mapToGlobal(self._more.rect().bottomLeft()))


class _PaneLegend(QtWidgets.QWidget):
    """The price-pane legend: a top-left stack of _LegendRow widgets (one per overlay/pattern
    indicator). Re-emits each row's signals tagged with the indicator uid."""

    editRequested = QtCore.Signal(int)
    removeRequested = QtCore.Signal(int)
    hideToggled = QtCore.Signal(int)
    moveRequested = QtCore.Signal(int, str)
    actionRequested = QtCore.Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background:transparent;")
        self._box = QtWidgets.QVBoxLayout(self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(1)
        self._rows = {}  # uid -> _LegendRow

    def rebuild(self, indicators):
        while self._box.count():
            w = self._box.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._rows = {}
        for ind in indicators:
            row = _LegendRow(ind, self)
            row.editRequested.connect(self.editRequested)
            row.removeRequested.connect(self.removeRequested)
            row.hideToggled.connect(self.hideToggled)
            row.moveRequested.connect(self.moveRequested)
            row.actionRequested.connect(self.actionRequested)
            self._box.addWidget(row)
            self._rows[ind.uid] = row
        self.adjustSize()

    def set_value(self, uid, text):
        row = self._rows.get(uid)
        if row is not None:
            row.set_value(text)


class _ObjectTree(dropdowns.PopupCard):
    """TradingView-style Object Tree: every active indicator grouped by where it lives (price
    pane vs each oscillator pane), each a legend row with the full ⋯ menu / eye / double-click.
    Rebuilds whenever the chart's indicator set changes."""

    def __init__(self, chart: "PriceChart", parent=None):
        super().__init__(parent, object_name="treeCard", extra_qss="QLabel{background:transparent;}")
        self._chart = chart
        self.resize_card(300, 380)
        card = self.card
        self._v = QtWidgets.QVBoxLayout(card)
        self._v.setContentsMargins(14, 12, 14, 12)
        self._v.setSpacing(6)
        head = QtWidgets.QHBoxLayout()
        t = QtWidgets.QLabel("Object tree")
        t.setStyleSheet(f"color:{theme.TEXT};font-size:14px;font-weight:700;background:transparent;")
        x = QtWidgets.QPushButton("✕")
        x.setFlat(True)
        x.setStyleSheet(f"QPushButton{{background:transparent;border:none;color:{theme.TEXT3};}}")
        x.clicked.connect(self.reject)
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(x)
        self._v.addLayout(head)
        self._body = QtWidgets.QVBoxLayout()
        self._body.setSpacing(2)
        self._v.addLayout(self._body)
        self._v.addStretch(1)
        self.rebuild()

    def _group(self, title):
        lbl = QtWidgets.QLabel(title)
        lbl.setStyleSheet(
            f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;"
            f"background:transparent;margin-top:4px;"
        )
        self._body.addWidget(lbl)

    def _row(self, ind):
        row = _LegendRow(ind)
        row.editRequested.connect(self._chart.edit_indicator)
        row.removeRequested.connect(lambda u: (self._chart.remove_indicator(u), self.rebuild()))
        row.hideToggled.connect(lambda u: (self._chart._toggle_visible(u), self.rebuild()))
        row.moveRequested.connect(lambda u, t: (self._chart.move_indicator(u, t), self.rebuild()))
        row.actionRequested.connect(lambda u, a: (self._chart._indicator_action(u, a), self.rebuild()))
        self._body.addWidget(row)

    def rebuild(self):
        while self._body.count():
            item = self._body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        inds = list(self._chart._indicators.values())
        on_price = [i for i in inds if i.kind in ("overlay", "pattern")]
        if on_price:
            self._group("PRICE")
            for ind in on_price:
                self._row(ind)
        for n, pane in enumerate(self._chart._osc_panes(), 1):
            self._group(f"PANE {n}")
            for ind in inds:
                if ind.pane is pane:
                    self._row(ind)
        if not inds:
            note = QtWidgets.QLabel("No indicators on the chart.")
            note.setStyleSheet(f"color:{theme.TEXT3};background:transparent;")
            self._body.addWidget(note)


class OscillatorPane(pg.PlotWidget):
    """A stacked sub-pane hosting one or MORE oscillator/pairs indicators (merged), x-linked to
    the price chart and revealed in lockstep. Its header is a stack of _LegendRow widgets (one
    per indicator). Management requests are re-emitted tagged with the indicator uid."""

    editRequested = QtCore.Signal(int)
    removeRequested = QtCore.Signal(int)
    hideToggled = QtCore.Signal(int)
    moveRequested = QtCore.Signal(int, str)
    actionRequested = QtCore.Signal(int, str)
    dragMoved = QtCore.Signal(object, int)  # (pane, cursor global y) — drag-to-reorder
    dragEnded = QtCore.Signal()
    # pane-level (carry the pane, so a multi-indicator merged pane moves/deletes atomically)
    paneMoveUp = QtCore.Signal(object)
    paneMoveDown = QtCore.Signal(object)
    paneMaximizeToggled = QtCore.Signal(object)
    paneDeleteRequested = QtCore.Signal(object)

    def __init__(self, link_to: "PriceChart"):
        _time_axis = TimeAxis(orientation="bottom")
        super().__init__(axisItems={"right": PriceAxis(orientation="right"),
                                    "bottom": _time_axis})
        self._time_axis = _time_axis
        self._inds = []           # list[_Indicator] hosted in this pane
        self._curves = {}         # uid -> {output label: PlotDataItem}
        self._rows = {}           # uid -> _LegendRow
        # transparent (like the price chart) so the rounded card bg shows through; full repaint
        # avoids translucent-viewport trails.
        self.setBackground(None)
        _vp = self.viewport()
        _vp.setAutoFillBackground(False)
        _vp.setStyleSheet("background:transparent;")
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
        self.showAxis("right")
        self.hideAxis("left")
        # The bottom time axis is only SHOWN on the lowest pane (PriceChart._reassign_bottom_axis);
        # kept hidden here so non-lowest panes align via x-link without a duplicated axis strip.
        self.hideAxis("bottom")
        self.getAxis("right").setTextPen(theme.TEXT3)
        _transparent = pg.mkPen(QtGui.QColor(0, 0, 0, 0))
        self.getAxis("right").setPen(_transparent)
        self.getAxis("right").setTickPen(pg.mkPen(theme.BORDER))
        self.getAxis("right").setStyle(tickLength=0)
        # Bottom time axis styled exactly like the price chart's (transparent spine, BORDER
        # gridline pen, mono tick font) so the lowest pane's axis matches the rest of the chrome.
        _bottom = self.getAxis("bottom")
        _bottom.setTextPen(theme.TEXT3)
        _bottom.setPen(_transparent)
        _bottom.setTickPen(pg.mkPen(theme.BORDER))
        _bottom.setStyle(tickLength=0, tickFont=_mono_tick_font())
        # Splitter floor so a pane never collapses below a readable height, independent of
        # _resize_panes (Phase 2 disables that while a pane is maximized).
        self.setMinimumHeight(64)
        self.showGrid(x=True, y=True, alpha=_GRID)
        self.hideButtons()
        self.getViewBox().setMouseEnabled(x=False, y=False)  # driven by the price chart
        self.getViewBox().setXLink(link_to.getViewBox())     # follow the price x-range

        self._header = QtWidgets.QWidget(self)  # grip + stacked legend rows, top-left
        self._header.setStyleSheet("background:transparent;")
        _hh = QtWidgets.QHBoxLayout(self._header)
        _hh.setContentsMargins(0, 0, 0, 0)
        _hh.setSpacing(4)
        self._grip = _DragGrip(self._header)
        self._grip.dragged.connect(lambda y: self.dragMoved.emit(self, y))
        self._grip.released.connect(self.dragEnded)
        _hh.addWidget(self._grip, 0, QtCore.Qt.AlignTop)
        _rowscol = QtWidgets.QWidget(self._header)
        _rowscol.setStyleSheet("background:transparent;")
        self._hbox = QtWidgets.QVBoxLayout(_rowscol)
        self._hbox.setContentsMargins(0, 0, 0, 0)
        self._hbox.setSpacing(0)
        _hh.addWidget(_rowscol)
        self._header.move(6, 3)

        # TradingView-style per-pane hover toolbar (move up/down / maximize / delete), top-right.
        self._toolbar = _PaneToolbar(self)
        self._toolbar.moveUp.connect(lambda: self.paneMoveUp.emit(self))
        self._toolbar.moveDown.connect(lambda: self.paneMoveDown.emit(self))
        self._toolbar.maximizeToggled.connect(lambda: self.paneMaximizeToggled.emit(self))
        self._toolbar.deletePane.connect(lambda: self.paneDeleteRequested.emit(self))
        self._toolbar.hide()
        # belt-and-braces: re-check cursor-in-rect before hiding so the bar survives a menu/popup.
        self._tb_timer = QtCore.QTimer(self)
        self._tb_timer.setInterval(120)
        self._tb_timer.setSingleShot(True)
        self._tb_timer.timeout.connect(self._maybe_hide_toolbar)

    @property
    def uids(self):
        return [i.uid for i in self._inds]

    def count(self) -> int:
        return len(self._inds)

    def has(self, uid: int) -> bool:
        return uid in self._rows

    def add_ind(self, ind: "_Indicator"):
        self._inds.append(ind)
        row = _LegendRow(ind, self._header)
        row.editRequested.connect(self.editRequested)
        row.removeRequested.connect(self.removeRequested)
        row.hideToggled.connect(self.hideToggled)
        row.moveRequested.connect(self.moveRequested)
        row.actionRequested.connect(self.actionRequested)
        self._hbox.addWidget(row)
        self._rows[ind.uid] = row
        self._build_curves(ind)
        self._header.adjustSize()

    def remove_ind(self, uid: int) -> int:
        """Remove one indicator; returns the number of indicators left in the pane."""
        for c in self._curves.pop(uid, {}).values():
            self.removeItem(c)
        row = self._rows.pop(uid, None)
        if row is not None:
            row.setParent(None)
            row.deleteLater()
        self._inds = [i for i in self._inds if i.uid != uid]
        self._header.adjustSize()
        return len(self._inds)

    def _build_curves(self, ind: "_Indicator"):
        cs = {}
        widths = getattr(ind, "widths", [1])
        styles = getattr(ind, "styles", ["solid"])
        for i, label in enumerate(ind.series):
            col = ind.colors[i % len(ind.colors)]
            pen = pg.mkPen(col, width=widths[i % len(widths)],
                           style=_pen_style(styles[i % len(styles)]))
            cs[label] = self.plot([], [], pen=pen)
        self._curves[ind.uid] = cs

    def update_ind(self, ind: "_Indicator"):
        """After an edit: rebuild that indicator's curves + refresh its legend row."""
        for c in self._curves.get(ind.uid, {}).values():
            self.removeItem(c)
        self._build_curves(ind)
        if ind.uid in self._rows:
            self._rows[ind.uid].refresh(ind)

    def reveal(self, index: int):
        all_ys = []
        for ind in self._inds:
            last = None
            for label, curve in self._curves.get(ind.uid, {}).items():
                series = ind.series.get(label, [])
                xs = [k for k in range(min(index + 1, len(series))) if series[k] is not None]
                ys = [series[k] for k in xs]
                curve.setData(xs, ys)
                curve.setVisible(ind.shown)
                if ind.shown:
                    all_ys += ys
                if ys:
                    last = ys[-1]
            if ind.uid in self._rows:
                self._rows[ind.uid].set_value(f"{last:,.2f}" if last is not None else "")
        if all_ys:
            lo, hi = min(all_ys), max(all_ys)
            if hi > lo:
                self.setYRange(lo, hi, padding=0.12)

    def refresh_legend(self):
        for ind in self._inds:
            if ind.uid in self._rows:
                self._rows[ind.uid].refresh(ind)

    def set_bars(self, bars):
        """Feed the pane's bottom time axis so its tick strings match the price chart's."""
        self._time_axis.set_bars(bars)

    def set_bottom_axis_visible(self, on: bool):
        """Show/hide this pane's bottom time axis (shown only on the lowest pane)."""
        self.showAxis("bottom") if on else self.hideAxis("bottom")

    def _position_toolbar(self):
        """Tuck the hover toolbar at the top-right, just left of the (shared-width) price axis."""
        tb = getattr(self, "_toolbar", None)
        if tb is None:
            return
        tb.adjustSize()
        # getAxis("right").width() here is the shared axis width equalised across all panes by
        # _sync_axis_width(); it is only valid after a prior layout pass (_align_panes /
        # show_upto / resizeEvent), which is why toolbar positioning is re-tucked from those
        # call sites rather than being a one-shot operation.
        axis_w = int(self.getAxis("right").width()) if self.getAxis("right").isVisible() else 0
        x = self.width() - axis_w - tb.width() - 4
        tb.move(max(0, x), 3)
        tb.raise_()

    def _cursor_in_rect(self) -> bool:
        return self.rect().contains(self.mapFromGlobal(QtGui.QCursor.pos()))

    def _maybe_hide_toolbar(self):
        if not self._cursor_in_rect():
            self._toolbar.hide()

    def enterEvent(self, e):  # noqa: N802 - Qt override
        self._position_toolbar()
        self._toolbar.show()
        self._toolbar.raise_()
        if e is not None:
            super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802 - Qt override
        # hide immediately when cursor is out; arm timer if cursor moved onto a child/popup
        if self._cursor_in_rect():
            self._tb_timer.start()   # cursor moved onto a child/popup: re-check shortly
        else:
            self._toolbar.hide()
        if e is not None:
            super().leaveEvent(e)

    def resizeEvent(self, e):  # noqa: N802 - Qt override
        super().resizeEvent(e)
        self._position_toolbar()

    def set_maximized(self, on: bool):
        """Delegate the maximize/restore glyph swap to the pane's hover toolbar."""
        tb = getattr(self, "_toolbar", None)
        if tb is not None:
            tb.set_maximized(on)


class PriceChart(pg.PlotWidget):
    """Candles + TradeStation-style trade markers + indicator overlays + a replay cursor,
    with TradingView-style chrome: time axis, mouse crosshair, OHLC legend header, a
    last-price line+badge, and vertical autoscale that fits the visible candles."""

    intervalChosen = QtCore.Signal(str)  # emitted by the timeframe dropdown (e.g. "5m")
    pairsRequested = QtCore.Signal(str)  # a pairs indicator was picked; the app supplies a benchmark

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
            self.getAxis(_ax).setStyle(tickLength=0, tickFont=_mono_tick_font())
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
        self._indicators = {}  # uid -> _Indicator (user-added; managed via the per-pane legend)
        self._pane_host = None  # the vertical QSplitter that stacks the price chart + osc panes
        self._price_legend = None  # _PaneLegend overlay listing the price-pane indicators
        self._marker_off = 0.0  # cached price-range marker offset (for pattern marker placement)
        self._z_top = 1.0  # running z for overlay visual-order (kept above the candles at z=0)
        self._chart_interval = None  # current timeframe (for per-interval indicator visibility)
        self._vb2 = None  # secondary ViewBox for overlays pinned to their own scale
        self._wsyncing = False  # re-entrancy guard for _sync_axis_width (mirrors _fitting)
        self._maximized_pane = None  # the pane currently maximized (locks _resize_panes)
        self._saved_sizes = None     # host.sizes() snapshot to restore on un-maximize

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
        self._last_badge = pg.TextItem(color=theme.BG, anchor=(0, 0.5), fill=pg.mkBrush(_UP))
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

        # symbol name at the FAR LEFT, before the timeframe selector (TradingView-style)
        self._symbol_label = QtWidgets.QLabel("", self._top_bar)
        self._symbol_label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self._symbol_label.setStyleSheet(
            f"color:{theme.TEXT};font-family:{theme.FONT_MONO};font-size:14px;"
            f"font-weight:700;background:transparent;padding:0 2px;"
        )
        _tb.addWidget(self._symbol_label, 0, QtCore.Qt.AlignVCenter)
        _tb.addWidget(_divider())

        # timeframe selector (grouped dropdown) -> emits intervalChosen
        self._tf_btn = QtWidgets.QPushButton("1m", self._top_bar)
        self._tf_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._tf_btn.setStyleSheet(_btn_qss)
        _tf_menu = QtWidgets.QMenu(self._tf_btn)
        # The toolbar's `QWidget{background:transparent}` rule cascades into this menu (same
        # specificity as the app-wide QMenu rule, but more local, so it would win and leave the
        # popup transparent/off-tone). Re-assert the unified dropdown surface explicitly here so the
        # timeframe menu matches every other popup (SURFACE card, BORDER edge, popup radius).
        _tf_menu.setStyleSheet(
            f"QMenu{{background:{theme.SURFACE};border:1px solid {theme.BORDER};"
            f"border-radius:{theme.RADIUS_POPUP}px;padding:4px;}}"
            f"QMenu::item{{padding:{theme.DROPDOWN_ITEM_PAD};border-radius:{theme.RADIUS_SM}px;"
            f"color:{theme.TEXT2};}}"
            f"QMenu::item:selected{{background:{theme.HOVER};color:{theme.TEXT};}}"
            f"QMenu::separator{{height:1px;background:{theme.BORDER};margin:4px 8px;}}"
        )
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
        _tag_qss = (f"color:{theme.TEXT};background:{theme.BORDER};border-radius:2px;padding:0 4px;"
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

        # price-pane legend (overlay + pattern indicators) with per-indicator controls
        self._price_legend = _PaneLegend(self)
        self._price_legend.editRequested.connect(self.edit_indicator)
        self._price_legend.removeRequested.connect(self.remove_indicator)
        self._price_legend.hideToggled.connect(self._toggle_visible)
        self._price_legend.moveRequested.connect(self.move_indicator)
        self._price_legend.actionRequested.connect(self._indicator_action)
        self._position_price_legend()

    # --- data ---
    def set_title(self, text: str):
        """Set the symbol shown at the far left of the toolbar (before the timeframe selector)."""
        self._title = text or ""
        self._symbol_label.setText(self._title)
        self._show_last_ohlc()

    def set_data(self, bars, trades):
        self._bars = bars
        self._recompute_indicators()  # persist user indicators across symbol/interval (recompute)
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
        self._align_panes()

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

    def apply_live(self, bars, overlays=None, *, repaint=True):
        """Refresh the series in place for a live tick — candles/axis/timestamps + overlays.

        Unlike ``set_data`` this keeps existing trade markers and any user-picked indicators
        (it *merges* ``overlays`` rather than replacing the lot), so a per-tick refresh never
        wipes the chart the user has set up. Live ticks only append at the tail, so existing
        markers' bar-indices stay valid. With ``repaint`` (default) it paints to the live edge;
        pass ``repaint=False`` to update the data only — e.g. while the user has scrolled back
        into history, the caller repaints via its own replay cursor instead of yanking the view.
        """
        self._bars = bars
        if self._indicators:  # extend user-picked indicators to the live edge (skip churn if none)
            self._recompute_indicators()
        self._time_axis.set_bars(bars)
        self._ts_index = {b.ts: i for i, b in enumerate(bars)}
        if overlays:
            for label in overlays:
                if label not in self._overlay_curves:
                    color = _OVERLAY_COLORS[len(self._overlay_curves) % len(_OVERLAY_COLORS)]
                    self._overlay_curves[label] = self.plot(
                        [], [], pen=pg.mkPen(color, width=1), name=label
                    )
            self._overlays = {**self._overlays, **overlays}
        if repaint:
            self.show_upto(len(bars) - 1)
        self._align_panes()

    # --- indicators (TradingView-style: pick from the catalog, overlay on the chart) ---
    def _open_indicator_picker(self):
        dlg = _IndicatorPicker(self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dlg.chosen.connect(self.add_indicator)
        btn = getattr(self, "_ind_btn", None)  # drop it just under the ƒx Indicators button
        if btn is not None:
            dlg.move(btn.mapToGlobal(QtCore.QPoint(-_CARD_SHADOW, btn.height() + 4 - _CARD_SHADOW)))
        self._ind_dlg = dlg  # keep a ref; dismisses on outside-click / pick (WA_DeleteOnClose)
        dlg.show()
        dlg.activateWindow()
        dlg.raise_()

    def _on_splitter_moved(self, *_):
        """A manual splitter drag exits maximize (like TV) and re-tags the pane toolbars."""
        if self._maximized_pane is not None:
            pane = self._maximized_pane
            self._maximized_pane = None
            self._saved_sizes = None
            if pane is not None:
                pane.set_maximized(False)
        self._refresh_pane_toolbars()

    def set_pane_host(self, splitter):
        """Give the chart the vertical QSplitter it shares with its oscillator sub-panes."""
        self._pane_host = splitter
        splitter.splitterMoved.connect(self._on_splitter_moved)

    def add_indicator(self, name: str, params=None, benchmark=None):
        """Add a user indicator, routed by kind: price-overlay on the candles, oscillator in a
        stacked pane, candlestick pattern as bar markers, or pairs (vs a 2nd symbol) in a pane.
        Returns the ``_Indicator`` handle (None if it can't be added / awaits a benchmark)."""
        if not self._bars:
            return None
        from vike_trader_app.core.indicators import base as _base

        try:
            spec = _base.get(name)
        except Exception:  # noqa: BLE001 - unknown indicator
            return None
        if spec.category == "pairs" and benchmark is None:
            self.pairsRequested.emit(name)  # app prompts for a 2nd symbol -> add_pairs()
            return None
        kind = ("pairs" if spec.category == "pairs"
                else "pattern" if spec.category == "pattern"
                else "overlay" if name in _OVERLAY_NAMES else "oscillator")
        if params is None:
            params = {p.name: p.default for p in spec.params}
        ind = _Indicator(name, spec, params, kind)
        ind.benchmark = benchmark
        self._indicators[ind.uid] = ind
        self._compute(ind)
        self._render(ind)
        self._refresh_legends()
        return ind

    def add_pairs(self, name: str, benchmark: list):
        """Add a pairs indicator computed against a 2nd symbol's aligned closes."""
        return self.add_indicator(name, benchmark=benchmark)

    def _data_cols(self) -> dict:
        return {
            "open": [b.open for b in self._bars], "high": [b.high for b in self._bars],
            "low": [b.low for b in self._bars], "close": [b.close for b in self._bars],
            "volume": [b.volume for b in self._bars],
        }

    def _compute(self, ind: "_Indicator"):
        """Run the indicator with its current params -> ``ind.series`` {output label -> series}."""
        from vike_trader_app.core.indicators import base as _base

        data = self._data_cols()
        if ind.kind == "pairs":
            data["benchmark"] = ind.benchmark or []
        try:
            result = _base.compute(ind.name, data, **ind.params)
        except Exception:  # noqa: BLE001 - bad inputs -> empty
            ind.series = {}
            return

        def _clean(seq):  # nan/inf -> None so the renderer skips warm-up gaps
            return [None if v is None or (isinstance(v, float) and v != v) else v for v in seq]

        outs = ind.spec.outputs
        if ind.kind == "pattern":
            ind.series = {outs[0]: [int(v) for v in result]}
        elif len(outs) <= 1:
            ind.series = {outs[0]: _clean(result)}
        else:
            ind.series = {lbl: _clean(s) for lbl, s in zip(outs, result)}

    # --- render / reveal / lifecycle, per indicator ---
    def _new_pane(self) -> "OscillatorPane":
        pane = OscillatorPane(self)
        pane.editRequested.connect(self.edit_indicator)
        pane.removeRequested.connect(self.remove_indicator)
        pane.hideToggled.connect(self._toggle_visible)
        pane.moveRequested.connect(self.move_indicator)
        pane.actionRequested.connect(self._indicator_action)
        pane.dragMoved.connect(self._drag_pane)
        pane.paneMoveUp.connect(self._pane_move_up)
        pane.paneMoveDown.connect(self._pane_move_down)
        pane.paneMaximizeToggled.connect(self._toggle_maximize_pane)
        pane.paneDeleteRequested.connect(self._delete_pane)
        self._pane_host.addWidget(pane)
        pane.set_bars(self._bars)  # so the fresh pane's time axis isn't blank
        self._resize_panes()
        self._refresh_pane_toolbars()
        return pane

    def _drag_pane(self, pane, global_y: int):
        """Live drag-to-reorder: swap with the neighbour once dragged past its vertical centre."""
        host = self._pane_host
        if host is None:
            return
        cur = host.indexOf(pane)
        if cur < 1:
            return
        if cur > 1:  # try to move above the upper neighbour
            up = host.widget(cur - 1)
            ctr = up.mapToGlobal(QtCore.QPoint(0, up.height() // 2)).y()
            if global_y < ctr:
                # exit maximize before reordering so _resize_panes can re-lay-out freely
                if self._maximized_pane is not None:
                    prev_max = self._maximized_pane
                    self._maximized_pane = None
                    self._saved_sizes = None
                    prev_max.set_maximized(False)
                host.insertWidget(cur - 1, pane)
                self._resize_panes()
                self._align_panes()
                self._refresh_pane_toolbars()
                return
        if cur < host.count() - 1:  # try to move below the lower neighbour
            down = host.widget(cur + 1)
            ctr = down.mapToGlobal(QtCore.QPoint(0, down.height() // 2)).y()
            if global_y > ctr:
                # exit maximize before reordering so _resize_panes can re-lay-out freely
                if self._maximized_pane is not None:
                    prev_max = self._maximized_pane
                    self._maximized_pane = None
                    self._saved_sizes = None
                    prev_max.set_maximized(False)
                host.insertWidget(cur + 1, pane)
                self._resize_panes()
                self._align_panes()
                self._refresh_pane_toolbars()

    def _next_z(self) -> float:
        self._z_top += 1.0
        return self._z_top

    def _render(self, ind: "_Indicator"):
        if ind.kind == "overlay":
            ind.curves = {}
            widths = getattr(ind, "widths", [1])
            styles = getattr(ind, "styles", ["solid"])
            for i, lbl in enumerate(ind.series):
                col = ind.colors[i % len(ind.colors)]
                pen = pg.mkPen(col, width=widths[i % len(widths)],
                               style=_pen_style(styles[i % len(styles)]))
                curve = pg.PlotDataItem([], [], pen=pen)
                if ind.own_scale:                 # independent right scale (secondary viewbox)
                    self._ensure_vb2()
                    self._vb2.addItem(curve)
                else:
                    self.addItem(curve)
                    curve.setZValue(self._next_z())  # overlays sit above the candles
                ind.curves[lbl] = curve
        elif ind.kind in ("oscillator", "pairs"):
            if self._pane_host is None:
                return
            if ind.pane is None:           # fresh add -> its own pane (merge sets ind.pane first)
                ind.pane = self._new_pane()
            ind.pane.add_ind(ind)
            self._align_panes()            # merge path: pane pre-set, so realign here too
        elif ind.kind == "pattern":
            ind.scatter = pg.ScatterPlotItem(hoverable=True, pen=None,
                                             tip=lambda x, y, data: str(data))
            ind.scatter.setZValue(20)
            self.addItem(ind.scatter)
        self._reveal_indicator(ind, self._reveal_index())
        self._apply_visibility(ind)

    def _unrender(self, ind: "_Indicator"):
        for c in ind.curves.values():
            if ind.own_scale and self._vb2 is not None:
                self._vb2.removeItem(c)
            else:
                self.removeItem(c)
        ind.curves = {}
        if ind.pane is not None:
            remaining = ind.pane.remove_ind(ind.uid)
            if remaining == 0:           # last indicator left the pane -> drop the pane
                if ind.pane is self._maximized_pane:
                    self._maximized_pane = None  # avoid a dangling deleted-QWidget ref
                ind.pane.setParent(None)
                ind.pane.deleteLater()
                self._resize_panes()
                self._align_panes()      # after setParent(None): host no longer counts the pane
            ind.pane = None
        if ind.scatter is not None:
            self.removeItem(ind.scatter)
            ind.scatter = None

    def _reveal_index(self) -> int:
        return (len(self._candles._bars) - 1) if self._candles._bars else len(self._bars) - 1

    def _sync_shown(self, ind: "_Indicator"):
        """Effective visibility = user toggle AND (no interval restriction OR current one allowed)."""
        ind.shown = ind.visible and (
            ind.intervals is None or self._chart_interval in ind.intervals
        )

    def _reveal_indicator(self, ind: "_Indicator", index: int):
        if index < 0:
            return
        self._sync_shown(ind)
        if ind.kind == "overlay":
            for lbl, curve in ind.curves.items():
                series = ind.series.get(lbl, [])
                xs = [k for k in range(min(index + 1, len(series))) if series[k] is not None]
                curve.setData(xs, [series[k] for k in xs])
                curve.setVisible(ind.shown)
        elif ind.kind in ("oscillator", "pairs") and ind.pane is not None:
            ind.pane.reveal(index)
        elif ind.kind == "pattern" and ind.scatter is not None:
            self._reveal_pattern(ind, index)

    def _reveal_pattern(self, ind: "_Indicator", index: int):
        series = next(iter(ind.series.values()), [])
        off = self._marker_off
        label = _pretty_indicator(ind.name)
        spots = []
        if ind.shown:
            for i in range(min(index + 1, len(series))):
                v = series[i]
                if not v:
                    continue
                bull = v > 0
                bar = self._bars[i]
                y = (bar.low - off) if bull else (bar.high + off)
                spots.append({
                    "pos": (i, y), "symbol": "t1" if bull else "t", "size": 12,
                    "brush": pg.mkBrush(theme.UP if bull else theme.DOWN), "pen": None,
                    "data": f"{label} · {'Bullish' if bull else 'Bearish'}",
                })
        ind.scatter.setData(spots)

    def remove_indicator(self, uid: int):
        ind = self._indicators.pop(uid, None)
        if ind is not None:
            self._unrender(ind)
            self._refresh_legends()

    def _toggle_visible(self, uid: int):
        ind = self._indicators.get(uid)
        if ind is not None:
            self.set_indicator_visible(uid, not ind.visible)

    def set_indicator_visible(self, uid: int, visible: bool):
        ind = self._indicators.get(uid)
        if ind is None:
            return
        ind.visible = visible
        self._apply_visibility(ind)
        self._reveal_indicator(ind, self._reveal_index())
        self._refresh_legends()

    def _apply_visibility(self, ind: "_Indicator"):
        self._sync_shown(ind)
        for c in ind.curves.values():
            c.setVisible(ind.shown)
        if ind.scatter is not None:
            ind.scatter.setVisible(ind.shown)

    def edit_indicator(self, uid: int):
        """Open the Settings dialog (Inputs + Style); apply -> recompute + re-render."""
        ind = self._indicators.get(uid)
        if ind is None:
            return
        dlg = _IndicatorSettings(ind, self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dlg.applied.connect(lambda params, colors, u=uid: self._apply_edit(u, params, colors))
        dlg.exec()

    def _apply_edit(self, uid: int, params: dict, colors: list):
        ind = self._indicators.get(uid)
        if ind is None:
            return
        ind.params = params
        ind.colors = colors or ind.colors
        if ind.kind in ("oscillator", "pairs") and ind.pane is not None:
            self._compute(ind)
            ind.pane.update_ind(ind)
            ind.pane.reveal(self._reveal_index())
        else:
            self._unrender(ind)
            self._compute(ind)
            self._render(ind)
        self._refresh_legends()

    def clone_indicator(self, uid: int):
        """Duplicate an indicator (same params/colours) — TradingView's 'Clone'."""
        ind = self._indicators.get(uid)
        if ind is None:
            return None
        clone = self.add_indicator(ind.name, params=dict(ind.params), benchmark=ind.benchmark)
        if clone is not None and ind.colors:
            self._apply_edit(clone.uid, dict(clone.params), list(ind.colors))
        return clone

    def open_object_tree(self):
        """Open the Object Tree dialog (all active indicators grouped by pane)."""
        dlg = _ObjectTree(self, self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self._tree_dlg = dlg  # keep a ref
        btn = getattr(self, "_ind_btn", None)
        if btn is not None:
            dlg.move(btn.mapToGlobal(QtCore.QPoint(-_CARD_SHADOW, btn.height() + 4 - _CARD_SHADOW)))
        dlg.show()

    def _indicator_action(self, uid: int, action: str):
        ind = self._indicators.get(uid)
        if ind is None:
            return
        if action == "clone":
            self.clone_indicator(uid)
            return
        if action == "tree":
            self.open_object_tree()
            return
        if action in ("pin_own", "pin_price"):
            self._pin_overlay(ind, action == "pin_own")
            return
        if action.startswith("iv:"):  # toggle per-interval visibility for one timeframe
            self._toggle_interval_visibility(ind, action[3:])
            return
        if ind.kind == "overlay" and ind.curves:  # visual order (z-stacking) for overlays
            zs = [c.zValue() for c in ind.curves.values()]
            if action == "front":
                z = self._next_z()
            elif action == "back":
                z = 0.5  # still above the candles (z=0)
            elif action == "forward":
                z = max(zs) + 1
            else:  # backward
                z = max(0.5, min(zs) - 1)
            for c in ind.curves.values():
                c.setZValue(z)

    def move_indicator(self, uid: int, target: str):
        """Move an indicator between panes. ``target``: 'price' (overlay on the candles),
        'new' (its own oscillator pane), 'up'/'down' (reorder its pane), or
        'merge_above'/'merge_below' (merge into the adjacent pane)."""
        ind = self._indicators.get(uid)
        if ind is None:
            return
        if target == "price":
            if ind.kind != "overlay":
                self._unrender(ind)
                ind.kind = "overlay"
                ind.pane = None
                self._render(ind)
        elif target == "new":
            self._unrender(ind)
            ind.kind = "oscillator"
            ind.pane = None  # _render gives it a fresh pane
            self._render(ind)
        elif target in ("up", "down"):
            self._reorder_pane(ind, target)
            return
        elif target in ("merge_above", "merge_below"):
            self._merge_into_adjacent(ind, target)
        self._align_panes()  # finalize alignment after the unrender/render churn settles
        self._refresh_legends()

    def _reorder_pane(self, ind: "_Indicator", direction: str):
        host = self._pane_host
        if host is None or ind.pane is None:
            return
        idx = host.indexOf(ind.pane)
        new = idx - 1 if direction == "up" else idx + 1
        if 1 <= new <= host.count() - 1:   # keep below the price chart (index 0)
            host.insertWidget(new, ind.pane)
            self._resize_panes()
            self._align_panes()

    def _merge_into_adjacent(self, ind: "_Indicator", direction: str):
        host = self._pane_host
        if host is None or ind.pane is None:
            return
        idx = host.indexOf(ind.pane)
        tgt = idx - 1 if direction == "merge_above" else idx + 1
        if not (1 <= tgt <= host.count() - 1):
            return  # no adjacent oscillator pane (e.g. the price chart is above)
        target_pane = host.widget(tgt)
        if not isinstance(target_pane, OscillatorPane):
            return
        self._unrender(ind)                # detach from the current pane (drops it if now empty)
        ind.kind = "oscillator"
        ind.pane = target_pane             # _render adds it into the existing pane
        self._render(ind)

    def _pane_move_up(self, pane):
        """Move a whole pane up one slot via its hover toolbar (keyed off the pane object, so a
        merged multi-indicator pane moves atomically). Clamped to index >= 1 (never above price)."""
        host = self._pane_host
        if host is None:
            return
        idx = host.indexOf(pane)
        if idx <= 1:
            return  # already topmost oscillator pane (price is fixed at index 0)
        host.insertWidget(idx - 1, pane)
        self._after_pane_reorder()

    def _pane_move_down(self, pane):
        host = self._pane_host
        if host is None:
            return
        idx = host.indexOf(pane)
        if idx < 1 or idx >= host.count() - 1:
            return  # already the bottom pane
        host.insertWidget(idx + 1, pane)
        self._after_pane_reorder()

    def _after_pane_reorder(self):
        """Common tail for a toolbar-driven reorder: resize, re-tag toolbars, and realign the
        shared axis + bottom-time axis to the new lowest pane (Phase 1)."""
        self._resize_panes()
        self._align_panes()
        self._refresh_pane_toolbars()

    def _delete_pane(self, pane):
        """Delete a whole pane via its toolbar: remove every indicator it hosts (the last
        removal triggers `_unrender`'s empty-pane teardown), then re-tag the survivors. Null any
        dangling maximized-pane lock so we don't reference a deleted QWidget."""
        if pane is self._maximized_pane:
            self._maximized_pane = None
        for uid in list(pane.uids):
            self.remove_indicator(uid)
        self._refresh_pane_toolbars()

    def _toggle_maximize_pane(self, pane):
        """Toggle a pane between maximized and the normal stacked layout (TradingView's pane
        maximize). Maximizing keeps a real price floor so OHLC stays visible; restoring replays
        the user's pre-maximize splitter proportions when the pane count is unchanged."""
        host = self._pane_host
        if host is None:
            return
        if pane is self._maximized_pane:        # --- restore ---
            self._maximized_pane = None
            if self._saved_sizes is not None and len(self._saved_sizes) == host.count():
                host.setSizes(self._saved_sizes)  # preserve user-dragged proportions (TV)
            else:
                self._resize_panes()
            self._saved_sizes = None
            pane.set_maximized(False)
        else:                                    # --- maximize ---
            self._saved_sizes = host.sizes()
            self._maximized_pane = pane
            total = sum(self._saved_sizes) or (host.height() or 600)
            n = host.count()
            idx = host.indexOf(pane)
            price_floor = max(140, int(total * 0.15))
            others = max(1, n - 2)               # panes that aren't price and aren't maximized
            slim = 1                             # minimal share for the non-maximized panes
            big = max(price_floor, total - price_floor - slim * others)
            sizes = [slim] * n
            sizes[0] = price_floor
            sizes[idx] = big
            host.setSizes(sizes)
            pane.set_maximized(True)
        self._refresh_pane_toolbars()

    def _panes_in_visual_order(self):
        """Oscillator panes in top-to-bottom splitter order (NOT dict-insertion order).
        Use this everywhere pane *order* matters — the bottom time axis and shared
        axis width key off the lowest pane, which `_osc_panes()` (dict order) can't track
        after a drag/reorder."""
        host = self._pane_host
        if host is None:
            return []
        return [host.widget(i) for i in range(1, host.count())
                if isinstance(host.widget(i), OscillatorPane)]

    def _axis_natural_width(self, axis) -> float:
        """The width a right AxisItem *would* take for its CURRENT tick strings, computed
        synchronously via QFontMetrics — paint-independent so headless tests can assert it
        immediately. Mirrors pyqtgraph's AxisItem._updateWidth:
            textWidth + style['tickTextOffset'][0] + max(0, style['tickLength']).
        Reading axis.width() instead is unsafe: in pyqtgraph 0.14.0 it returns geometry from
        the *last* layout pass, so it is stale (or 0) right after setWidth()."""
        if not axis.isVisible():
            return 0.0
        mn, mx = axis.range
        size = axis.height() or 300
        try:
            levels = axis.tickValues(mn, mx, size)
        except Exception:  # noqa: BLE001 - degenerate range -> no strings to measure
            levels = []
        strings = []
        for spacing, values in levels:
            try:
                strings += [s for s in axis.tickStrings(values, axis.scale, spacing) if s]
            except Exception:  # noqa: BLE001
                pass
        font = axis.style.get("tickFont") or axis.font()
        fm = QtGui.QFontMetrics(font)
        text_w = max((fm.horizontalAdvance(s) for s in strings), default=axis.textWidth)
        return float(text_w + axis.style["tickTextOffset"][0] + max(0, axis.style["tickLength"]))

    def _sync_axis_width(self):
        """Pin every pane's right price axis (and the price chart's) to one shared width so
        plot columns are pixel-aligned in time. Width is the max natural width across axes,
        computed synchronously (no dependence on a pending paint). When there are no panes,
        the price axis is restored to auto so a lone chart isn't stuck at a stale pinned width."""
        if self._wsyncing:
            return
        self._wsyncing = True
        try:
            panes = self._panes_in_visual_order()
            price_ax = self.getAxis("right")
            if not panes:
                price_ax.setWidth(None)  # lone chart -> auto width
                self.getPlotItem().layout.activate()
                return
            axes = [price_ax] + [p.getAxis("right") for p in panes]
            w = int(round(max(self._axis_natural_width(a) for a in axes)))
            for a in axes:
                a.setWidth(w)
            self.getPlotItem().layout.activate()
            for p in panes:
                p.getPlotItem().layout.activate()
        finally:
            self._wsyncing = False

    def _reassign_bottom_axis(self):
        """Keep exactly one visible bottom time axis, on the LOWEST pane (TradingView puts the
        time scale under the lowest pane, not under the candles). With no panes the price chart
        keeps its own bottom axis."""
        panes = self._panes_in_visual_order()
        if not panes:
            self.showAxis("bottom")
            self._time_axis.set_bars(self._bars)
        else:
            self.hideAxis("bottom")
            for p in panes:
                p.set_bottom_axis_visible(False)
                p.set_bars(self._bars)
            panes[-1].set_bottom_axis_visible(True)  # lowest splitter index = bottom
        # hideAxis/showAxis only INVALIDATE the layout (lazy); force it + re-sync the own-scale
        # viewbox now so own-scale overlays don't lag behind the grown/shrunk price ViewBox.
        self.getPlotItem().layout.activate()
        self._sync_vb2()
        self._autorange_vb2()

    def _align_panes(self):
        """Re-align every pane in time after any layout/lifecycle change. Idempotent and safe
        with zero panes. Order matters: reassign the bottom axis FIRST (it changes which axes
        are visible and their natural widths), THEN equalize the right-axis width across the
        now-correct set of axes."""
        self._reassign_bottom_axis()
        self._sync_axis_width()

    def _osc_panes(self):
        seen, panes = set(), []
        for i in self._indicators.values():
            if i.pane is not None and id(i.pane) not in seen:
                seen.add(id(i.pane))
                panes.append(i.pane)
        return panes

    def _clear_indicators(self):
        for ind in list(self._indicators.values()):
            self._unrender(ind)
        self._indicators = {}
        self._refresh_legends()

    def _recompute_indicators(self):
        """Persist user indicators across a new symbol/interval by recomputing them on the new
        bars (render handles + panes are kept). Pairs are dropped — their benchmark was aligned
        to the previous bars. show_upto() (called next) reveals the refreshed series."""
        for ind in list(self._indicators.values()):
            if ind.kind == "pairs":
                self._unrender(ind)
                self._indicators.pop(ind.uid, None)
                continue
            self._compute(ind)
        self._refresh_legends()

    # --- pin-to-scale: overlays on an independent (own) right scale via a secondary ViewBox ---
    def _ensure_vb2(self):
        if self._vb2 is not None:
            return
        self._vb2 = pg.ViewBox()
        self.scene().addItem(self._vb2)
        self._vb2.setXLink(self.getViewBox())
        self.getViewBox().sigResized.connect(self._sync_vb2)
        self._sync_vb2()

    def _sync_vb2(self):
        if self._vb2 is not None:
            self._vb2.setGeometry(self.getViewBox().sceneBoundingRect())

    def _autorange_vb2(self):
        """Fit the secondary viewbox to the visible data of all own-scale overlays."""
        if self._vb2 is None:
            return
        idx = self._reveal_index()
        ys = []
        for ind in self._indicators.values():
            if ind.kind == "overlay" and ind.own_scale and ind.shown:
                for s in ind.series.values():
                    ys += [s[k] for k in range(min(idx + 1, len(s))) if s[k] is not None]
        if ys and max(ys) > min(ys):
            self._vb2.setYRange(min(ys), max(ys), padding=0.1)

    def _pin_overlay(self, ind: "_Indicator", own: bool):
        if ind.kind != "overlay" or ind.own_scale == own:
            return
        ind.own_scale = own
        for c in list(ind.curves.values()):
            if own:
                self.getPlotItem().removeItem(c)
                self._ensure_vb2()
                self._vb2.addItem(c)
            else:
                if self._vb2 is not None:
                    self._vb2.removeItem(c)
                self.getPlotItem().addItem(c)
                c.setZValue(self._next_z())
        self.show_upto(self._reveal_index())

    def _toggle_interval_visibility(self, ind: "_Indicator", interval: str):
        """Toggle whether ``ind`` shows on ``interval``. ``ind.intervals`` is None when it shows
        on all timeframes; otherwise it's the explicit set of allowed timeframes."""
        all_iv = _all_intervals()
        cur = set(ind.intervals) if ind.intervals is not None else set(all_iv)
        cur.discard(interval) if interval in cur else cur.add(interval)
        ind.intervals = _normalize_intervals(cur)
        self._apply_visibility(ind)
        self._reveal_indicator(ind, self._reveal_index())
        self._refresh_legends()

    def _resize_panes(self):
        """Give the price chart the bulk of the height; each oscillator pane ~22% (stacked).
        The LOWEST pane gets an extra axis-strip (~20px) so its PLOT area matches its siblings'
        (the bottom time axis lives there); cosmetic only — x-alignment is independent.
        No-op while a pane is maximized so add/remove/reorder don't stomp the maximized layout."""
        host = self._pane_host
        if host is None or host.count() <= 1:
            return
        if self._maximized_pane is not None:
            return
        n_panes = host.count() - 1
        total = host.height() or 600
        axis_strip = 20  # bottom time-axis height on the lowest pane
        pane_h = max(96, int(total * 0.22))
        price_h = max(140, total - pane_h * n_panes - axis_strip)
        sizes = [price_h] + [pane_h] * n_panes
        sizes[-1] += axis_strip  # lowest pane carries the axis strip
        host.setSizes(sizes)

    def _refresh_pane_toolbars(self):
        """Sync every pane's hover-toolbar state to its current visual position: up enabled when
        a pane is above, down enabled when a pane is below, max glyph reflecting the maximized
        pane. Also re-tucks each toolbar left of the (now-settled) shared right axis."""
        panes = self._panes_in_visual_order()
        n = len(panes)
        for p, pane in enumerate(panes):
            tb = getattr(pane, "_toolbar", None)
            if tb is None:
                continue
            tb.set_can_up(p > 0)
            tb.set_can_down(p < n - 1)
            tb.set_maximized(pane is self._maximized_pane)
            pane._position_toolbar()

    def _refresh_legends(self):
        """Rebuild the price-pane legend (overlay + pattern indicators) + refresh pane headers."""
        if self._price_legend is not None:
            on_price = [i for i in self._indicators.values() if i.kind in ("overlay", "pattern")]
            self._price_legend.rebuild(on_price)
            self._position_price_legend()
        for ind in self._indicators.values():
            if ind.pane is not None:
                ind.pane.refresh_legend()

    def _position_price_legend(self):
        legend = getattr(self, "_price_legend", None)  # may not exist yet during super().__init__
        if legend is not None:
            legend.move(10, 36)  # just under the OHLC toolbar, top-left
            legend.raise_()

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
        self._marker_off = marker_off  # cached so pattern markers re-place on hide/show + edits
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
        for ind in self._indicators.values():  # reveal user indicators (overlay/osc/pattern)
            self._reveal_indicator(ind, index)
        self._autorange_vb2()  # fit own-scale overlays on their independent right axis
        # axis label width only settles once data is revealed; re-tuck the pane toolbars so they
        # clear the (now-known) shared right axis.
        if self._pane_host is not None and self._panes_in_visual_order():
            self._refresh_pane_toolbars()

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
            self._ohlc_label.setText("")  # symbol lives in the far-left toolbar label now
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
        self._ohlc_label.setText(body)  # no symbol/interval prefix — symbol is the far-left label
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
        if self._panes_in_visual_order():
            # the time axis moved to the lowest pane -> this tag would float over the price plot
            # with no axis beneath it. Hide it (Phase 4 adds the bottom-pane time tag).
            self._cx_time_tag.hide()
        else:
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
        if getattr(self, "_pane_host", None) is not None and self._panes_in_visual_order():
            self._align_panes()  # width settles after a resize -> re-equalize before reading it
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
        self._position_price_legend()

    def set_timeframe(self, interval: str):
        """Update the timeframe selector label + current interval, and refresh per-interval
        indicator visibility (indicators restricted to other timeframes hide here)."""
        self._chart_interval = interval
        if hasattr(self, "_tf_btn"):
            self._tf_btn.setText(interval)
        if self._bars:
            for ind in self._indicators.values():
                self._sync_shown(ind)
                self._reveal_indicator(ind, self._reveal_index())
        self._align_panes()


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
