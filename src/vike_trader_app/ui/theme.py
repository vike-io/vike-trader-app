"""vike.io visual theme: color tokens + a Qt stylesheet (QSS).

Palette extracted from the live vike.io CSS — GitHub-dark canvas, orange brand
accent, JetBrains Mono throughout. Shared by the widgets and the pyqtgraph charts.
"""

# --- color tokens ---
# Deeper near-black canvas (matches the "terminal-inside-a-product" depth of
# polished trading studios) while keeping the GitHub-dark blue undertone + the
# vike.io orange accent.
BG = "#0a0c10"
PANEL = "#13171e"
PANEL2 = "#1a1f28"
RAISE = "#21262d"
HOVER = "#1c2129"
BORDER = "#272d37"
BORDER2 = "#3d444d"
TEXT = "#e6edf3"
TEXT2 = "#9aa4b1"
TEXT3 = "#6b7480"
ACCENT = "#ff6a00"
ACCENT_HOVER = "#ff8534"
BLUE = "#58a6ff"
UP = "#3fb950"
DOWN = "#f85149"
FAST = "#58a6ff"
SLOW = "#a855f7"
WARN = "#ffb000"
ROW_ODD = "#0f1319"

# verdict colors
VERDICT = {"Low": UP, "Medium": WARN, "High": DOWN}

# Typography: a sans UI font for all chrome (buttons, labels, tabs, headings) and
# a monospace face reserved for code + tabular numbers. Mixing the two — instead of
# mono-everywhere — is the single biggest "product vs. raw dev-tool" visual cue.
FONT_UI = '"Inter", "Segoe UI", system-ui, "Helvetica Neue", sans-serif'
FONT_MONO = '"JetBrains Mono", "Cascadia Code", Consolas, monospace'
FONT = FONT_MONO  # back-compat alias


def stylesheet() -> str:
    """Return the application-wide QSS implementing the vike look."""
    return f"""
    * {{
        font-family: {FONT_UI};
        font-size: 13px;
        color: {TEXT};
    }}
    QMainWindow, QWidget {{ background: {BG}; }}

    /* dock panels */
    QDockWidget {{
        titlebar-close-icon: none; titlebar-normal-icon: none;
        color: {TEXT2}; font-size: 11px; font-weight: 700;
    }}
    QDockWidget::title {{
        background: {PANEL2}; padding: 8px 12px;
        border: 1px solid {BORDER}; border-bottom: none;
        text-transform: uppercase; letter-spacing: 1px;
    }}
    QDockWidget > QWidget {{ border: 1px solid {BORDER}; }}

    /* generic panels */
    .Panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}

    /* group boxes (e.g. the Tools-tab calculators) — themed card with an inset title */
    QGroupBox {{
        background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px;
        margin-top: 14px; padding: 10px 12px 12px 12px;
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
        padding: 8px 16px; margin-right: 2px;
        border: none; border-bottom: 2px solid transparent;
        font-size: 13px; font-weight: 600;
    }}
    QTabBar::tab:hover {{ color: {TEXT2}; }}
    QTabBar::tab:selected {{
        color: {TEXT}; border-bottom: 2px solid {ACCENT};
    }}

    /* buttons */
    QPushButton {{
        background: {RAISE}; color: {TEXT}; border: 1px solid {BORDER};
        border-radius: 8px; padding: 8px 15px; font-weight: 600;
    }}
    QPushButton:hover {{ background: #262c36; border-color: {BORDER2}; }}
    QPushButton:pressed {{ background: {PANEL2}; }}
    QPushButton:disabled {{ color: {TEXT3}; background: {PANEL}; border-color: {BORDER}; }}
    QPushButton#play {{
        background: {ACCENT}; color: #1a0d00; border: none; font-weight: 700;
    }}
    QPushButton#play:hover {{ background: {ACCENT_HOVER}; }}
    QPushButton#validate {{
        background: rgba(255,176,0,0.10); color: {WARN};
        border: 1px solid rgba(255,176,0,0.45); font-weight: 600;
    }}
    QPushButton#validate:hover {{ background: rgba(255,176,0,0.18); }}

    /* combo / inputs */
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit {{
        background: {RAISE}; color: {TEXT}; border: 1px solid {BORDER};
        border-radius: 8px; padding: 7px 11px; selection-background-color: {ACCENT};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QDateEdit:focus {{ border-color: {ACCENT}; }}
    QComboBox:hover {{ border-color: {BORDER2}; }}
    QComboBox QAbstractItemView {{
        background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px;
        selection-background-color: {ACCENT}; selection-color: {BG}; padding: 4px;
    }}

    /* slider */
    QSlider::groove:horizontal {{ height: 5px; background: {BG}; border: 1px solid {BORDER}; border-radius: 5px; }}
    QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 5px; }}
    QSlider::handle:horizontal {{
        background: {ACCENT}; width: 13px; margin: -5px 0; border-radius: 7px;
        border: 2px solid {BG};
    }}

    /* tables — mono cells for tabular alignment, sans uppercase headers */
    QTableWidget, QTableView {{
        background: {PANEL}; alternate-background-color: {ROW_ODD};
        gridline-color: transparent; border: none; font-size: 12px;
        font-family: {FONT_MONO};
    }}
    QTableWidget::item {{ padding: 5px 8px; color: {TEXT2}; }}
    QTableWidget::item:selected {{ background: {HOVER}; color: {TEXT}; }}
    QHeaderView::section {{
        background: {PANEL}; color: {TEXT3}; padding: 8px 8px;
        border: none; border-bottom: 1px solid {BORDER};
        font-family: {FONT_UI}; font-size: 10px; font-weight: 700;
        text-transform: uppercase; letter-spacing: 0.5px;
    }}

    /* lists */
    QListWidget {{ background: {PANEL}; border: none; outline: none; }}
    QListWidget::item {{ border-bottom: 1px solid rgba(48,54,61,0.4); padding: 2px; }}
    QListWidget::item:selected {{ background: {HOVER}; }}

    /* scrollbars */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {RAISE}; border-radius: 5px; min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: {BORDER2}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; }}
    QScrollBar::handle:horizontal {{ background: {RAISE}; border-radius: 5px; min-width: 28px; }}

    /* splitter */
    QSplitter::handle {{ background: {BG}; }}
    QSplitter::handle:hover {{ background: {BORDER}; }}

    /* dialogs */
    QDialog {{ background: {BG}; }}

    QToolTip {{
        background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER};
        border-radius: 6px; padding: 4px 8px;
    }}
    """
