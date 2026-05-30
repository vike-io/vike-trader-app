"""vike.io visual theme: color tokens + a Qt stylesheet (QSS).

Palette extracted from the live vike.io CSS — GitHub-dark canvas, orange brand
accent, JetBrains Mono throughout. Shared by the widgets and the pyqtgraph charts.
"""

# --- color tokens ---
BG = "#0d1117"
PANEL = "#161b22"
PANEL2 = "#1c2129"
RAISE = "#21262d"
HOVER = "#1c2129"
BORDER = "#30363d"
BORDER2 = "#484f58"
TEXT = "#e6edf3"
TEXT2 = "#8b949e"
TEXT3 = "#6e7681"
ACCENT = "#ff6a00"
ACCENT_HOVER = "#ff8534"
BLUE = "#58a6ff"
UP = "#3fb950"
DOWN = "#f85149"
FAST = "#58a6ff"
SLOW = "#a855f7"
WARN = "#ffb000"
ROW_ODD = "#11161d"

# verdict colors
VERDICT = {"Low": UP, "Medium": WARN, "High": DOWN}

FONT = "JetBrains Mono, Consolas, monospace"


def stylesheet() -> str:
    """Return the application-wide QSS implementing the vike look."""
    return f"""
    * {{
        font-family: "JetBrains Mono", Consolas, monospace;
        font-size: 12px;
        color: {TEXT};
    }}
    QMainWindow, QWidget {{ background: {BG}; }}

    /* dock panels */
    QDockWidget {{
        titlebar-close-icon: none; titlebar-normal-icon: none;
        color: {TEXT2}; font-size: 11px; font-weight: 600;
    }}
    QDockWidget::title {{
        background: {PANEL2}; padding: 7px 10px;
        border: 1px solid {BORDER}; border-bottom: none;
        text-transform: uppercase; letter-spacing: 1px;
    }}
    QDockWidget > QWidget {{ border: 1px solid {BORDER}; }}

    /* generic panels */
    .Panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}

    /* buttons */
    QPushButton {{
        background: {RAISE}; color: {TEXT}; border: 1px solid {BORDER};
        border-radius: 7px; padding: 7px 12px;
    }}
    QPushButton:hover {{ background: #262c36; border-color: {BORDER2}; }}
    QPushButton:pressed {{ background: {PANEL2}; }}
    QPushButton#play {{
        background: {ACCENT}; color: {BG}; border: none; font-weight: 700;
    }}
    QPushButton#play:hover {{ background: {ACCENT_HOVER}; }}
    QPushButton#validate {{
        background: rgba(255,176,0,0.10); color: {WARN};
        border: 1px solid rgba(255,176,0,0.45); font-weight: 600;
    }}
    QPushButton#validate:hover {{ background: rgba(255,176,0,0.18); }}

    /* combo / inputs */
    QComboBox, QLineEdit, QSpinBox {{
        background: {RAISE}; color: {TEXT}; border: 1px solid {BORDER};
        border-radius: 7px; padding: 6px 9px;
    }}
    QComboBox:hover {{ border-color: {BORDER2}; }}
    QComboBox QAbstractItemView {{
        background: {PANEL}; border: 1px solid {BORDER};
        selection-background-color: {ACCENT}; selection-color: {BG};
    }}

    /* slider */
    QSlider::groove:horizontal {{ height: 5px; background: {BG}; border: 1px solid {BORDER}; border-radius: 5px; }}
    QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 5px; }}
    QSlider::handle:horizontal {{
        background: {ACCENT}; width: 13px; margin: -5px 0; border-radius: 7px;
        border: 2px solid {BG};
    }}

    /* tables */
    QTableWidget, QTableView {{
        background: {PANEL}; alternate-background-color: {ROW_ODD};
        gridline-color: transparent; border: none; font-size: 11px;
    }}
    QTableWidget::item {{ padding: 3px 6px; color: {TEXT2}; }}
    QTableWidget::item:selected {{ background: {HOVER}; color: {TEXT}; }}
    QHeaderView::section {{
        background: {PANEL2}; color: {TEXT3}; padding: 6px 8px;
        border: none; border-bottom: 1px solid {BORDER};
        font-size: 9px; font-weight: 600; text-transform: uppercase;
    }}

    /* lists */
    QListWidget {{ background: {PANEL}; border: none; outline: none; }}
    QListWidget::item {{ border-bottom: 1px solid rgba(48,54,61,0.4); }}
    QListWidget::item:selected {{ background: {HOVER}; }}

    /* scrollbars */
    QScrollBar:vertical {{ background: transparent; width: 9px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {RAISE}; border-radius: 4px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {BORDER2}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 9px; }}
    QScrollBar::handle:horizontal {{ background: {RAISE}; border-radius: 4px; min-width: 24px; }}

    /* splitter */
    QSplitter::handle {{ background: {BG}; }}
    QSplitter::handle:hover {{ background: {BORDER}; }}

    QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; }}
    """
