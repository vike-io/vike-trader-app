"""vike.io visual theme: HSL-authored color tokens + shape/spacing scale + a Qt stylesheet.

One blue (GitHub-dark) hue family for every surface (hue 215) and all text (hue 214),
with a single green accent. Colors are authored in HSL via ``hsl()`` and emitted as hex,
so both the QSS and pyqtgraph consume them unchanged. Shared by the widgets and charts.

Surface model (Option A — "chrome is one flat tone"):
  * BG       -> chrome: window, chart title bar, bottom bar, left rail, dock titles, dialogs.
  * SURFACE  -> floating content: panels, tables, lists, group boxes, inputs, buttons,
                combo popups, menus, tooltips, scrollbar handles.
  * HOVER    -> hover / selected feedback.
  * BORDER   -> every border, and the chart grid.
"""

from __future__ import annotations

from colorsys import hls_to_rgb

# NOTE: no top-level Qt import — this module stays pure-Python so the headless
# analysis layer (tearsheet, interactive) can import the color tokens too. The only
# Qt-dependent helper (apply_shadow) imports PySide6 lazily inside its body.


def hsl(h: float, s: float, l: float) -> str:
    """Author a color in HSL (h in degrees, s and l in percent); return ``#rrggbb``."""
    r, g, b = hls_to_rgb(h / 360.0, l / 100.0, s / 100.0)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


# --- surfaces: one hue (215); lightness does the work (7 -> 11 -> 15 -> 21) ---
BG = hsl(215, 28, 7)        # canvas / chrome: window, title bar, bottom bar, rail, chart, dialogs
SURFACE = hsl(215, 21, 11)  # floating content: panels, tables, inputs, buttons, menus, tooltips
HOVER = hsl(215, 15, 15)    # hover / selected
BORDER = hsl(215, 12, 21)   # every border + the chart grid

# Back-compat aliases — the 9 old surface/border tokens collapse onto the 4 above, so
# existing call sites keep working while the palette holds only the distinct values.
CHART_BG = BG
PANEL = SURFACE
PANEL2 = SURFACE
RAISE = SURFACE
BORDER2 = BORDER
ROW_ODD = BG

# --- text: one hue (214) ---
TEXT = hsl(214, 36, 93)
TEXT2 = hsl(214, 13, 65)
TEXT3 = hsl(214, 9, 46)

# --- accent + semantic (own hues, deliberately distinct) ---
ACCENT = hsl(148, 72, 56)
ACCENT_HOVER = hsl(148, 74, 65)
ON_ACCENT = hsl(148, 40, 10)  # dark text/icon sitting on the green accent
BLUE = hsl(212, 100, 67)
UP = hsl(128, 49, 49)
DOWN = hsl(3, 93, 63)
WARN = hsl(41, 100, 50)
# candle bodies/wicks — sampled 1:1 from the live TradeLocker chart, kept softer than UP/DOWN
CANDLE_UP = hsl(153, 43, 55)
CANDLE_DOWN = hsl(358, 64, 59)
# chart overlay/series accents
FAST = BLUE
SLOW = hsl(271, 91, 65)

# verdict colors (single source — consumers should use this, not local dicts)
VERDICT = {"Low": UP, "Medium": WARN, "High": DOWN}

# --- shape + spacing scale (unify radii/paddings the way colors are unified) ---
RADIUS_SM = 6
RADIUS_MD = 8
RADIUS_LG = 10
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
CONTROL_H = 32  # unified control height for buttons / inputs

# --- dropdown / popup unification (one spec for every dropdown) ---
ARROW_PX = 18                   # icon box for EVERY chevron (dropdown ▾, combo, ‹ › nav, collapse); glyph ≈ 14×14
FONT_DROPDOWN = 16              # field text size for every dropdown/combo (matches TradingView's 16px)
RADIUS_POPUP = RADIUS_LG        # one radius for every floating popup surface (menus, combo lists, popovers, cards)
DROPDOWN_ITEM_PAD = "8px 12px"  # one item-row padding (~32px row) for menus, combo lists, popover rows
CARD_MARGIN = 30                # translucent margin reserved around frameless popup cards (room for the shadow)

# --- typography: sans UI font for chrome, mono for code + tabular numbers ---
FONT_UI = '"Inter", "Segoe UI", system-ui, "Helvetica Neue", sans-serif'
FONT_MONO = '"JetBrains Mono", "Cascadia Code", Consolas, monospace'
FONT = FONT_MONO  # back-compat alias


def color_for(value: float) -> str:
    """Token color for a signed number: green up, red down, muted at zero."""
    if value > 0:
        return UP
    if value < 0:
        return DOWN
    return TEXT2


def apply_shadow(widget, *, radius: int = 24, y: int = 8, alpha: int = 170) -> None:
    """Soft drop shadow so floating SURFACE cards (menus, popups, dialogs) lift off the flat chrome."""
    from PySide6 import QtGui, QtWidgets

    eff = QtWidgets.QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(radius)
    eff.setOffset(0, y)
    eff.setColor(QtGui.QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(eff)


def apply_popup_shadow(widget) -> None:
    """The single drop shadow for every floating popup (cards, popovers) — tight and clearly
    visible. A wide faint shadow (radius 28 / alpha 130) read as empty dead-space around small
    popovers, so this is a narrower, stronger drop that fits a slim translucent margin."""
    apply_shadow(widget, radius=18, y=7, alpha=180)


_ARROW_PNG: dict[str, str] = {}


def _combo_arrow_png(color: str) -> str:
    """Render once (cached) a thin down-chevron PNG for QComboBox::down-arrow, returning a
    QSS-friendly (forward-slash) path so combo arrows match the unified chevrons used elsewhere
    (the filter pills, week-nav, collapse toggle) instead of the native filled triangle.

    Qt is imported lazily so importing the color tokens from the headless analysis layer stays
    pure-Python; this runs only from stylesheet(), which is GUI-only."""
    cached = _ARROW_PNG.get(color)
    if cached:
        return cached
    import os
    import tempfile

    from PySide6 import QtCore, QtGui

    # Same 48px canvas, path and 3.0 stroke as icons._draw_chevron_down, so the combo arrow is
    # pixel-identical to the chevrons drawn for the pills / nav / collapse (kept in sync by hand —
    # theme can't import icons without a cycle).
    pm = QtGui.QPixmap(48, 48)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(3.0)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    path = QtGui.QPainterPath()
    path.moveTo(7, 7)
    path.lineTo(24, 41)
    path.lineTo(41, 7)
    p.drawPath(path)
    p.end()
    out = os.path.join(tempfile.gettempdir(), f"vike_combo_arrow_{color.lstrip('#')}.png")
    pm.save(out)
    _ARROW_PNG[color] = out.replace("\\", "/")
    return _ARROW_PNG[color]


def stylesheet() -> str:
    """Return the application-wide QSS implementing the vike look."""
    combo_arrow = _combo_arrow_png(TEXT2)
    return f"""
    * {{
        font-family: {FONT_UI};
        font-size: 14px;
        color: {TEXT};
    }}
    QMainWindow, QWidget {{ background: {BG}; }}

    /* dock panels — title bar is chrome (BG), body content sits on SURFACE cards */
    QDockWidget {{
        titlebar-close-icon: none; titlebar-normal-icon: none;
        color: {TEXT2}; font-size: 11px; font-weight: 700;
    }}
    QDockWidget::title {{
        background: {BG}; padding: {SPACE_2}px {SPACE_3}px;
        border: 1px solid {BORDER}; border-bottom: none;
        text-transform: uppercase; letter-spacing: 1px;
    }}
    QDockWidget > QWidget {{ border: 1px solid {BORDER}; }}

    /* generic panels / cards */
    .Panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px; }}

    /* group boxes (e.g. the Tools-tab calculators) — themed card with an inset title */
    QGroupBox {{
        background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px;
        margin-top: 14px; padding: {SPACE_3}px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; subcontrol-position: top left;
        left: 12px; padding: 2px 6px; color: {TEXT2};
        font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
    }}

    /* top-level + results tabs — clean underline-on-accent, generous hit area */
    QTabWidget::pane {{ border: none; top: -1px; }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{
        background: transparent; color: {TEXT3};
        padding: {SPACE_2}px {SPACE_4}px; margin-right: 2px;
        border: none; border-bottom: 2px solid transparent;
        font-size: 13px; font-weight: 600;
    }}
    QTabBar::tab:hover {{ color: {TEXT2}; }}
    QTabBar::tab:selected {{
        color: {TEXT}; border-bottom: 2px solid {ACCENT};
    }}

    /* buttons — SURFACE card separated by its border (GitHub-style).
       NOTE: every font-weight rule MUST also set an explicit font-size. The base `*` rule sizes
       fonts in px (pointSize() == -1); a weight-only rule makes Qt's stylesheet engine re-apply
       setPointSize(-1) -> "QFont::setPointSize: Point size <= 0 (-1)" spam at startup. */
    QPushButton {{
        background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
        border-radius: {RADIUS_MD}px; padding: 7px 14px; min-height: 16px;
        font-size: 14px; font-weight: 600;
    }}
    QPushButton:hover {{ background: {HOVER}; }}
    QPushButton:pressed {{ background: {BG}; }}
    QPushButton:disabled {{ color: {TEXT3}; background: {SURFACE}; border-color: {BORDER}; }}
    QPushButton#play {{
        background: {ACCENT}; color: {ON_ACCENT}; border: none; font-size: 14px; font-weight: 700;
    }}
    QPushButton#play:hover {{ background: {ACCENT_HOVER}; }}
    QPushButton#validate {{
        background: rgba(255,176,0,0.10); color: {WARN};
        border: 1px solid rgba(255,176,0,0.45); font-size: 14px; font-weight: 600;
    }}
    QPushButton#validate:hover {{ background: rgba(255,176,0,0.18); }}

    /* combo / inputs */
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit {{
        background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
        border-radius: {RADIUS_MD}px; padding: 7px 11px; min-height: 16px;
        selection-background-color: {ACCENT}; selection-color: {ON_ACCENT};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QDateEdit:focus {{ border-color: {ACCENT}; }}
    QComboBox {{ font-size: {FONT_DROPDOWN}px; }}   /* unified dropdown field text size (TV: 16px) */
    /* combo arrow — the unified thin chevron (matches the filter pills / nav / collapse), not
       the native filled triangle */
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right;
        border: none; width: 24px; }}
    QComboBox::down-arrow {{ image: url({combo_arrow}); width: {ARROW_PX}px; height: {ARROW_PX}px; }}
    /* combo popup list — one popup radius + one item row, hover-highlighted (not accent) */
    QComboBox QAbstractItemView {{
        background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_POPUP}px;
        outline: none; padding: 4px;
    }}
    QComboBox QAbstractItemView::item {{
        padding: {DROPDOWN_ITEM_PAD}; border-radius: {RADIUS_SM}px; color: {TEXT2};
        min-height: 16px;
    }}
    QComboBox QAbstractItemView::item:selected,
    QComboBox QAbstractItemView::item:hover {{ background: {HOVER}; color: {TEXT}; }}

    /* menus — one popup radius + one item row (OS provides the drop shadow for top-level menus) */
    QMenu {{
        background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_POPUP}px;
        padding: 4px;
    }}
    QMenu::item {{ padding: {DROPDOWN_ITEM_PAD}; border-radius: {RADIUS_SM}px; color: {TEXT2}; }}
    QMenu::item:selected {{ background: {HOVER}; color: {TEXT}; }}
    QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

    /* slider */
    QSlider::groove:horizontal {{ height: 5px; background: {BG}; border: 1px solid {BORDER}; border-radius: 5px; }}
    QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 5px; }}
    QSlider::handle:horizontal {{
        background: {ACCENT}; width: 13px; margin: -5px 0; border-radius: 7px;
        border: 2px solid {BG};
    }}

    /* tables — mono cells for tabular alignment, sans uppercase headers */
    QTableWidget, QTableView {{
        background: {SURFACE}; alternate-background-color: {ROW_ODD};
        gridline-color: transparent; border: none; font-size: 13px;
        font-family: {FONT_MONO};
    }}
    QTableWidget::item {{ padding: 5px 8px; color: {TEXT2}; }}
    QTableWidget::item:hover {{ background: {HOVER}; }}
    QTableWidget::item:selected {{ background: {HOVER}; color: {TEXT}; }}
    QHeaderView::section {{
        background: {SURFACE}; color: {TEXT3}; padding: {SPACE_2}px;
        border: none; border-bottom: 1px solid {BORDER};
        font-family: {FONT_UI}; font-size: 12px; font-weight: 700;
        text-transform: uppercase; letter-spacing: 0.5px;
    }}

    /* lists */
    QListWidget {{ background: {SURFACE}; border: none; outline: none; }}
    QListWidget::item {{ border-bottom: 1px solid {BORDER}; padding: 2px; }}
    QListWidget::item:hover {{ background: {HOVER}; }}
    QListWidget::item:selected {{ background: {HOVER}; }}

    /* scrollbars — handle = BORDER so it reads on both BG and SURFACE */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: {TEXT3}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; }}
    QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 28px; }}
    QScrollBar::handle:horizontal:hover {{ background: {TEXT3}; }}

    /* splitter */
    QSplitter::handle {{ background: {BG}; }}
    QSplitter::handle:hover {{ background: {BORDER}; }}

    /* dock separators — a canvas-coloured gutter so panels read as separate cards */
    QMainWindow::separator {{ background: {BG}; width: 8px; height: 8px; }}
    QMainWindow::separator:hover {{ background: {BORDER}; }}

    /* dialogs */
    QDialog {{ background: {BG}; }}

    QToolTip {{
        background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
        border-radius: {RADIUS_SM}px; padding: 4px 8px;
    }}
    """
