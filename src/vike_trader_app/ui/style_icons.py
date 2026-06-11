"""Chart-style icons (TradingView-style) — one hand-drawn QPainter glyph per chart type.

The chart toolbar shows the CURRENT style as an icon-only button (like TradingView), and every
entry in the style dropdown carries its own distinctive monochrome line glyph. Drawn on a 36px
canvas (2× the 18px display size) so they stay crisp on HiDPI; recolored via the ``color`` arg
(defaults to the toolbar text tone). No asset files — same idiom as ``icons.py``.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui

from . import theme

_S = 36          # canvas (2x of the 18px display size)
_W = 2.4         # base pen width


def _painter(pm: QtGui.QPixmap, color: str, width: float = _W) -> QtGui.QPainter:
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    return p


def _R(x, y, w, h):
    return QtCore.QRectF(x, y, w, h)


def _L(x1, y1, x2, y2):
    return QtCore.QLineF(x1, y1, x2, y2)


def _poly(p, pts, *, close=False, brush=None):
    path = QtGui.QPainterPath()
    path.moveTo(*pts[0])
    for x, y in pts[1:]:
        path.lineTo(x, y)
    if close:
        path.closeSubpath()
    if brush is not None:
        p.fillPath(path, brush)
    p.drawPath(path)


def _alpha(color: str, a: int) -> QtGui.QColor:
    c = QtGui.QColor(color)
    c.setAlpha(a)
    return c


# --- glyph drawers (one per style; 36x36 canvas) ---------------------------------------------

def _candles(p, c, *, hollow=False, rounded=False, widths=(9, 9)):
    p.setBrush(QtCore.Qt.NoBrush if hollow else QtGui.QColor(c))
    w1, w2 = widths
    # up candle (left): wick 4..32, body 12..26 | down candle (right): wick 2..28, body 8..20
    for cx, wt, wb, bt, bb, w in ((12, 4, 32, 12, 26, w1), (25, 2, 28, 8, 20, w2)):
        p.drawLine(_L(cx, wt, cx, wb))
        rect = _R(cx - w / 2, bt, w, bb - bt)
        if rounded:
            p.drawRoundedRect(rect, 3, 3)
        else:
            p.drawRect(rect)


def _draw_candles(p, c):
    _candles(p, c)


def _draw_hollow(p, c):
    _candles(p, c, hollow=True)


def _draw_heikin(p, c):
    _candles(p, c, rounded=True)


def _draw_volcandles(p, c):
    _candles(p, c, widths=(6, 13))


def _draw_bars(p, c):  # OHLC: open tick left, close tick right
    for cx, t, b in ((12, 4, 30), (25, 8, 33)):
        p.drawLine(_L(cx, t, cx, b))
        p.drawLine(_L(cx - 6, t + 7, cx, t + 7))
        p.drawLine(_L(cx, b - 7, cx + 6, b - 7))


def _draw_hlc_bars(p, c):  # close tick (right) only
    for cx, t, b in ((12, 4, 30), (25, 8, 33)):
        p.drawLine(_L(cx, t, cx, b))
        p.drawLine(_L(cx, b - 7, cx + 6, b - 7))


def _draw_high_low(p, c):  # capped range bars
    for cx, t, b in ((12, 5, 30), (25, 8, 33)):
        p.drawLine(_L(cx, t, cx, b))
        p.drawLine(_L(cx - 4, t, cx + 4, t))
        p.drawLine(_L(cx - 4, b, cx + 4, b))


_ZIG = [(4, 26), (13, 14), (20, 21), (32, 6)]


def _draw_line(p, c):
    _poly(p, _ZIG)


def _draw_line_markers(p, c):
    _poly(p, _ZIG)
    p.setBrush(QtGui.QColor(c))
    for x, y in _ZIG:
        p.drawEllipse(QtCore.QPointF(x, y), 2.6, 2.6)


def _draw_step(p, c):
    _poly(p, [(4, 28), (12, 28), (12, 18), (21, 18), (21, 10), (32, 10)])


def _draw_area(p, c):
    fill = [(4, 26), (13, 14), (20, 21), (32, 6), (32, 33), (4, 33)]
    _poly(p, fill, close=True, brush=_alpha(c, 70))
    _poly(p, _ZIG)


def _draw_baseline(p, c):
    pen = p.pen()
    dash = QtGui.QPen(pen)
    dash.setStyle(QtCore.Qt.DashLine)
    dash.setWidthF(1.6)
    p.setPen(dash)
    p.drawLine(_L(3, 19, 33, 19))            # the baseline
    p.setPen(pen)
    _poly(p, [(4, 27), (12, 12), (20, 24), (32, 7)])  # price crossing it


def _draw_hlc_area(p, c):
    hi = [(4, 12), (14, 6), (24, 14), (32, 5)]
    lo = [(4, 28), (14, 22), (24, 30), (32, 21)]
    band = hi + lo[::-1]
    _poly(p, band, close=True, brush=_alpha(c, 60))
    _poly(p, hi)
    _poly(p, lo)


def _draw_columns(p, c):
    p.setBrush(QtGui.QColor(c))
    for x, h in ((6, 12), (15, 20), (24, 16), (33, 27)):
        p.drawRect(_R(x - 3, 33 - h, 6, h))


def _draw_renko(p, c):  # ascending staircase of bricks, alternating fill
    for i, (x, y, filled) in enumerate(((4, 22, True), (13, 14, False), (22, 6, True))):
        p.setBrush(QtGui.QColor(c) if filled else QtCore.Qt.NoBrush)
        p.drawRect(_R(x, y, 10, 10))


def _draw_range(p, c):  # equal-height range bars, mixed direction
    for x, y, filled in ((4, 16, True), (13, 8, False), (22, 16, False), (22, 4, True)):
        p.setBrush(QtGui.QColor(c) if filled else QtCore.Qt.NoBrush)
        p.drawRect(_R(x, y, 10, 12))


def _draw_line_break(p, c):  # tall thin blocks marching up
    for x, y, h, filled in ((5, 18, 14, True), (14, 10, 16, False), (23, 4, 18, True)):
        p.setBrush(QtGui.QColor(c) if filled else QtCore.Qt.NoBrush)
        p.drawRect(_R(x, y, 8, h))


def _draw_kagi(p, c):  # thick yang / thin yin angular path
    thick = QtGui.QPen(QtGui.QColor(c))
    thick.setWidthF(3.6)
    thick.setCapStyle(QtCore.Qt.RoundCap)
    thin = QtGui.QPen(QtGui.QColor(c))
    thin.setWidthF(1.5)
    thin.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(thick)
    _poly(p, [(6, 30), (6, 10), (16, 10)])
    p.setPen(thin)
    _poly(p, [(16, 10), (16, 24), (26, 24)])
    p.setPen(thick)
    _poly(p, [(26, 24), (26, 5), (32, 5)])


def _draw_pnf(p, c):  # X column + O column
    for x0 in (6,):
        for y0 in (6, 17):
            p.drawLine(_L(x0, y0, x0 + 9, y0 + 9))
            p.drawLine(_L(x0, y0 + 9, x0 + 9, y0))
    p.setBrush(QtCore.Qt.NoBrush)
    for y0 in (11, 22):
        p.drawEllipse(_R(21, y0, 9, 9))


_DRAWERS = {
    "Candles": _draw_candles,
    "Hollow candles": _draw_hollow,
    "Heikin Ashi": _draw_heikin,
    "Volume candles": _draw_volcandles,
    "Bars": _draw_bars,
    "HLC bars": _draw_hlc_bars,
    "High-low": _draw_high_low,
    "Line": _draw_line,
    "Line with markers": _draw_line_markers,
    "Step line": _draw_step,
    "Area": _draw_area,
    "Baseline": _draw_baseline,
    "HLC area": _draw_hlc_area,
    "Columns": _draw_columns,
    "Renko": _draw_renko,
    "Range": _draw_range,
    "Line break": _draw_line_break,
    "Kagi": _draw_kagi,
    "Point & Figure": _draw_pnf,
}

_cache: dict[tuple[str, str], QtGui.QIcon] = {}


def style_icon(style: str, color: str | None = None) -> QtGui.QIcon:
    """The glyph for a chart style (falls back to the candles glyph for unknown labels)."""
    color = color or theme.TEXT2
    key = (style, color)
    if key in _cache:
        return _cache[key]
    pm = QtGui.QPixmap(_S, _S)
    pm.fill(QtCore.Qt.transparent)
    p = _painter(pm, color)
    _DRAWERS.get(style, _draw_candles)(p, color)
    p.end()
    pm.setDevicePixelRatio(2.0)  # 36px canvas -> crisp 18px logical icon on HiDPI
    icon = QtGui.QIcon(pm)
    _cache[key] = icon
    return icon
