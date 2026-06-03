"""Hand-drawn vector icons for the left rail — crisp QPainter line-art, no asset files.

Each icon is drawn on a 48px canvas (2× the 24px display size, for DPI crispness) and
recolored per state: dim when idle, accent when the space/panel is active, mid on hover.
``rail_icon(name, off, on, hover)`` returns a multi-state QIcon for a QToolButton.
"""

from PySide6 import QtCore, QtGui

from . import theme

_S = 48


def _painter(pm: QtGui.QPixmap, color: str) -> QtGui.QPainter:
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(3.0)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    return p


def _R(x, y, w, h):
    return QtCore.QRectF(x, y, w, h)


def _P(x, y):
    return QtCore.QPointF(x, y)


def _draw_backtester(p, c):  # candlesticks
    p.setBrush(c)
    for cx, wt, wb, bt, bb in [(13, 10, 38, 16, 30), (24, 8, 34, 13, 25), (35, 14, 41, 20, 34)]:
        p.drawLine(QtCore.QLineF(cx, wt, cx, wb))
        p.drawRect(_R(cx - 4, bt, 8, bb - bt))


def _draw_studio(p, c):  # AI sparkle
    p.setBrush(c)
    pts = [(24, 8), (28, 20), (40, 24), (28, 28), (24, 40), (20, 28), (8, 24), (20, 20)]
    path = QtGui.QPainterPath()
    path.moveTo(*pts[0])
    for x, y in pts[1:]:
        path.lineTo(x, y)
    path.closeSubpath()
    p.drawPath(path)


def _draw_tools(p, c):  # sliders
    for y in (13, 24, 35):
        p.drawLine(QtCore.QLineF(9, y, 39, y))
    p.setBrush(QtGui.QColor(theme.BG))
    for y, kx in [(13, 18), (24, 31), (35, 14)]:
        p.drawEllipse(_P(kx, y), 4.2, 4.2)


def _draw_screener(p, c):  # 2x2 grid
    for x in (11, 27):
        for y in (11, 27):
            p.drawRoundedRect(_R(x, y, 10, 10), 2.5, 2.5)


def _draw_journal(p, c):  # list
    p.setBrush(c)
    for y in (14, 24, 34):
        p.drawEllipse(_P(11, y), 1.9, 1.9)
        p.drawLine(QtCore.QLineF(18, y, 39, y))


def _draw_alerts(p, c):  # bell
    path = QtGui.QPainterPath()
    path.moveTo(13, 32)
    path.lineTo(13, 23)
    path.cubicTo(13, 15, 18, 13, 24, 13)
    path.cubicTo(30, 13, 35, 15, 35, 23)
    path.lineTo(35, 32)
    p.drawPath(path)
    p.drawLine(QtCore.QLineF(9, 32, 39, 32))
    p.drawLine(QtCore.QLineF(24, 13, 24, 9))
    p.setBrush(c)
    p.drawEllipse(_P(24, 37), 2.6, 2.6)


def _draw_market(p, c):  # bid/ask arrows
    p.drawLine(QtCore.QLineF(17, 37, 17, 11))
    p.drawLine(QtCore.QLineF(17, 11, 12, 17))
    p.drawLine(QtCore.QLineF(17, 11, 22, 17))
    p.drawLine(QtCore.QLineF(31, 11, 31, 37))
    p.drawLine(QtCore.QLineF(31, 37, 26, 31))
    p.drawLine(QtCore.QLineF(31, 37, 36, 31))


def _draw_strategies(p, c):  # bot head
    p.drawLine(QtCore.QLineF(24, 9, 24, 14))
    p.setBrush(c)
    p.drawEllipse(_P(24, 8), 2.0, 2.0)
    p.setBrush(QtCore.Qt.NoBrush)
    p.drawRoundedRect(_R(13, 15, 22, 19), 4, 4)
    p.setBrush(c)
    p.drawEllipse(_P(20, 25), 2.3, 2.3)
    p.drawEllipse(_P(28, 25), 2.3, 2.3)


def _draw_chart(p, c):  # box-less line chart: a clean rising zig-zag (no pane outline)
    path = QtGui.QPainterPath()
    path.moveTo(9, 33)
    path.lineTo(19, 22)
    path.lineTo(27, 28)
    path.lineTo(39, 12)
    p.drawPath(path)


def _draw_trades(p, c):  # table
    p.drawRoundedRect(_R(10, 12, 28, 24), 3, 3)
    p.drawLine(QtCore.QLineF(10, 20, 38, 20))
    p.drawLine(QtCore.QLineF(24, 20, 24, 36))
    p.drawLine(QtCore.QLineF(10, 28, 38, 28))


def _draw_news(p, c):  # newspaper
    p.drawRoundedRect(_R(11, 13, 26, 24), 3, 3)
    p.setBrush(c)
    p.drawRect(_R(15, 17, 8, 6))                 # masthead block
    p.setBrush(QtCore.Qt.NoBrush)
    for y in (18, 22, 26):
        p.drawLine(QtCore.QLineF(26, y, 33, y))  # right column lines
    for y in (27, 31):
        p.drawLine(QtCore.QLineF(15, y, 23, y))  # lines below masthead


def _draw_data(p, c):  # database cylinder: 3 stacked disks + side walls
    for y in (11, 21, 31):
        p.drawEllipse(_R(13, y, 22, 7))
    p.drawLine(QtCore.QLineF(13, 14, 13, 34))
    p.drawLine(QtCore.QLineF(35, 14, 35, 34))


def _draw_options(p, c):  # option payoff hockey-stick + strike tick
    path = QtGui.QPainterPath()
    path.moveTo(9, 30)
    path.lineTo(24, 30)
    path.lineTo(39, 13)
    p.drawPath(path)
    p.drawLine(QtCore.QLineF(24, 34, 24, 22))  # strike marker at the kink


def _draw_calendar(p, c):  # calendar: framed grid, two top rings, day dots
    p.drawRoundedRect(_R(11, 14, 26, 23), 3, 3)
    p.drawLine(QtCore.QLineF(11, 22, 37, 22))   # header divider
    p.drawLine(QtCore.QLineF(18, 10, 18, 16))   # left hanging ring
    p.drawLine(QtCore.QLineF(30, 10, 30, 16))   # right hanging ring
    p.setBrush(c)
    for x in (17, 24, 31):
        for y in (28, 33):
            p.drawEllipse(_P(x, y), 1.3, 1.3)    # day dots


def _draw_save(p, c):  # floppy-disk save glyph: body + folded corner, shutter, label
    path = QtGui.QPainterPath()
    path.moveTo(13, 12)
    path.lineTo(30, 12)
    path.lineTo(36, 18)
    path.lineTo(36, 36)
    path.lineTo(13, 36)
    path.closeSubpath()
    p.drawPath(path)
    p.drawRect(_R(19, 12, 10, 7))               # shutter rectangle (upper area)
    p.drawRect(_R(17, 26, 14, 10))              # label rectangle (lower-middle)


def _draw_chevron_up(p, c):  # upward chevron
    path = QtGui.QPainterPath()
    path.moveTo(13, 29)
    path.lineTo(24, 18)
    path.lineTo(35, 29)
    p.drawPath(path)


def _draw_chevron_down(p, c):  # downward chevron
    path = QtGui.QPainterPath()
    path.moveTo(13, 19)
    path.lineTo(24, 30)
    path.lineTo(35, 19)
    p.drawPath(path)


def _draw_scale(p, c):  # balance/justice scale: central post on a base, top beam, two hanging pans
    p.drawLine(QtCore.QLineF(24, 12, 24, 33))    # central post
    p.drawLine(QtCore.QLineF(12, 16, 36, 16))    # top beam
    p.drawLine(QtCore.QLineF(18, 33, 30, 33))    # base foot
    for hx in (12, 36):                          # the two suspension drops
        p.drawLine(QtCore.QLineF(hx, 16, hx, 21))
    p.drawArc(_R(8, 21, 8, 7), 0, -180 * 16)     # left pan (shallow bowl)
    p.drawArc(_R(32, 21, 8, 7), 0, -180 * 16)    # right pan


def _draw_folder(p, c):  # simple folder with a tab
    path = QtGui.QPainterPath()
    path.moveTo(11, 17)
    path.lineTo(20, 17)
    path.lineTo(23, 20)
    path.lineTo(37, 20)
    path.lineTo(37, 34)
    path.lineTo(11, 34)
    path.closeSubpath()
    p.drawPath(path)


def _draw_gear(p, c):  # settings cog: a ring with 8 short radial teeth + a small inner hole
    import math
    cx, cy, r_in, r_out = 24.0, 24.0, 13.0, 17.0
    for i in range(8):
        a = math.radians(i * 45)
        ca, sa = math.cos(a), math.sin(a)
        p.drawLine(QtCore.QLineF(cx + r_in * ca, cy + r_in * sa,
                                 cx + r_out * ca, cy + r_out * sa))
    p.drawEllipse(_P(cx, cy), r_in, r_in)        # outer ring
    p.drawEllipse(_P(cx, cy), 4.5, 4.5)          # inner hole


_DRAW = {
    "backtester": _draw_backtester, "studio": _draw_studio, "tools": _draw_tools,
    "screener": _draw_screener, "journal": _draw_journal, "alerts": _draw_alerts,
    "market": _draw_market, "strategies": _draw_strategies, "trades": _draw_trades,
    "chart": _draw_chart, "news": _draw_news, "data": _draw_data, "calendar": _draw_calendar,
    "options": _draw_options, "save": _draw_save,
    "chevron_up": _draw_chevron_up, "chevron_down": _draw_chevron_down,
    "scale": _draw_scale, "folder": _draw_folder, "gear": _draw_gear,
}


def _pixmap(name: str, color: str) -> QtGui.QPixmap:
    pm = QtGui.QPixmap(_S, _S)
    pm.fill(QtCore.Qt.transparent)
    p = _painter(pm, color)
    fn = _DRAW.get(name)
    if fn is not None:
        fn(p, QtGui.QColor(color))
    p.end()
    return pm


def glyph_icon(name: str, color: str) -> QtGui.QIcon:
    """A single-state QIcon of one line-art glyph (for inline buttons / toolbars)."""
    return QtGui.QIcon(_pixmap(name, color))


def avatar(text: str, bg: str, fg: str = theme.BG) -> QtGui.QPixmap:
    """A round token / currency badge with a 1–2 char label (Market-watch instrument icon).

    True national-flag art needs bundled image assets (and Windows has no flag-emoji glyphs),
    so these coloured initial badges are the asset-free stand-in.
    """
    pm = QtGui.QPixmap(_S, _S)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor(bg))
    p.drawEllipse(3, 3, 42, 42)
    p.setPen(QtGui.QColor(fg))
    font = p.font()
    font.setPixelSize(23 if len(text) <= 1 else 17)
    font.setBold(True)
    p.setFont(font)
    p.drawText(QtCore.QRectF(0, 0, _S, _S), QtCore.Qt.AlignCenter, text)
    p.end()
    return pm


def brand_pixmap(size: int, bg: str, fg: str) -> QtGui.QPixmap:
    """The vike brand 'V' mark: an accent rounded-square with a dark V — same look as the
    left-rail badge, rendered at an arbitrary size for the window/taskbar app icon."""
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.setPen(QtCore.Qt.NoPen)
    inset = size * 0.06  # keep the rounded square off the very edge so corners aren't clipped
    p.setBrush(QtGui.QColor(bg))
    p.drawRoundedRect(_R(inset, inset, size - 2 * inset, size - 2 * inset), size * 0.26, size * 0.26)
    p.setPen(QtGui.QColor(fg))
    font = p.font()
    font.setPixelSize(max(int(size * 0.6), 6))
    font.setBold(True)
    p.setFont(font)
    p.drawText(_R(0, 0, size, size), QtCore.Qt.AlignCenter, "V")
    p.end()
    return pm


def brand_icon(bg: str, fg: str) -> QtGui.QIcon:
    """Multi-resolution app/window icon of the brand 'V' mark (16–256px for crisp title bar
    + taskbar rendering at any DPI)."""
    ic = QtGui.QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(brand_pixmap(s, bg, fg))
    return ic


def rail_icon(name: str, off: str, on: str, hover: str | None = None) -> QtGui.QIcon:
    """A QToolButton icon: ``off`` idle, ``on`` when checked, ``hover`` under the mouse."""
    ic = QtGui.QIcon()
    ic.addPixmap(_pixmap(name, off), QtGui.QIcon.Normal, QtGui.QIcon.Off)
    ic.addPixmap(_pixmap(name, on), QtGui.QIcon.Normal, QtGui.QIcon.On)
    ic.addPixmap(_pixmap(name, hover or on), QtGui.QIcon.Active, QtGui.QIcon.Off)
    ic.addPixmap(_pixmap(name, on), QtGui.QIcon.Active, QtGui.QIcon.On)
    return ic
