"""Main menu (S3 of the shell-UX plan) — VS-Code-style title-bar menu bar.

``build_menu_bar(win)`` returns a QMenuBar with File / View / Go / Insert / Format / Window /
Help menus, each repopulated on ``aboutToShow`` so dynamic lists (workspaces, recents, open
windows) are always current. It lives IN the custom title bar (replacing the old hamburger ≡
and the left icon rail) and behaves like VS Code's: click opens, hover then switches menus.
It is a THIN routing layer over actions MainWindow already has — no business logic lives here.
"""

from __future__ import annotations

from PySide6 import QtWidgets

from . import chart_styles, theme
from .style_icons import style_icon


def _menu(parent, title: str) -> QtWidgets.QMenu:
    # NO local stylesheet and NO custom QStyle: the app-wide theme.stylesheet() QMenu rules
    # are THE unified dropdown style (same surface/hover/radius/typography as every popup).
    # A widget-level setStyle() bypasses QStyleSheetStyle and turns the menus black — the
    # keycap-chip shortcut experiment died of exactly that; shortcuts render natively
    # (right-aligned dim text, the VS Code look).
    return QtWidgets.QMenu(title, parent)


_BAR_QSS = (
    f"QMenuBar{{background:transparent;color:{theme.TEXT2};font-size:13px;font-weight:400;"
    f"border:none;padding:0 2px;}}"
    f"QMenuBar::item{{background:transparent;padding:5px 9px;border-radius:{theme.RADIUS_SM}px;}}"
    f"QMenuBar::item:selected{{background:{theme.HOVER};color:{theme.TEXT};}}"
    f"QMenuBar::item:pressed{{background:{theme.HOVER};color:{theme.TEXT};}}"
)

# View/Insert/Format dropped from the bar per the user (panel toggles keep their Ctrl
# shortcuts + palette commands; indicator picker + chart style live on the chart's own
# toolbar); their _fill_* helpers stay for the command palette / future use.
_SECTIONS = (("File", "_fill_file"),
             ("Window", "_fill_window"), ("Help", "_fill_help"))


def build_menu_bar(win) -> QtWidgets.QMenuBar:
    bar = QtWidgets.QMenuBar()
    bar.setNativeMenuBar(False)   # must render inside the custom title bar, never the OS one
    bar.setStyleSheet(_BAR_QSS)
    bar.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Preferred)
    for title, fill_name in _SECTIONS:
        fill = globals()[fill_name]
        sub = _menu(bar, title)
        sub.aboutToShow.connect(lambda s=sub, f=fill: (s.clear(), f(s, win)))
        bar.addMenu(sub)
    return bar


def _active_chart(win):
    """The chart the Insert/Format verbs target: the focused chart document, else the Chart space."""
    current = win.tabs.currentWidget()
    return current.chart if hasattr(current, "chart") else win.price


# --- File -------------------------------------------------------------------------------------

def _fill_file(m, win):
    # No 'New' submenu — it duplicated the Go menu verbatim (Open <tool> for all 8 tools + a
    # 'Chart window' twin of Go's 'New chart window') plus a vestigial 'Go to Chart' no-op
    # (show_space(0) on the only space). Open tools/charts from the Go menu or the title-bar
    # launchers; File now holds just the workspace + export/exit verbs.
    open_ws = _menu(m, "Open Workspace")
    for name in win._workspaces.names():
        open_ws.addAction(name, lambda n=name: win._apply_workspace(n))
    m.addMenu(open_ws)
    recents = _menu(m, "Recent Workspaces")
    rec = win._workspaces.recents()
    if rec:
        for name in rec:
            recents.addAction(name, lambda n=name: win._apply_workspace(n))
    else:
        a = recents.addAction("(none yet)")
        a.setEnabled(False)
    m.addMenu(recents)
    m.addAction("Save Workspace As…", win._prompt_save_workspace)
    user = [n for n in win._workspaces.names() if win._workspaces.is_user(n)]
    if user:
        delete = _menu(m, "Delete Workspace")
        for name in user:
            delete.addAction(name, lambda n=name: win._delete_workspace(n))
        m.addMenu(delete)
    m.addSeparator()
    m.addAction("AI: generate a layout…", win._prompt_ai_layout)
    m.addSeparator()
    m.addAction("Export chart image…", win._export_chart_image)
    m.addSeparator()
    m.addAction("Exit\tAlt+F4", win.close)


# --- View -------------------------------------------------------------------------------------

def _fill_view(m, win):
    # Only the core panels — the dashboard tiles (movers/pnl/ecal/headlines) are hidden from
    # the menu per the user (Calendar + News are now on-demand docks, opened via open_tool from
    # Go or the title-bar launchers); their Ctrl+Shift shortcuts + palette commands still work.
    core = {"backtester", "market", "trades"}
    for key, _icon, tip, sc in win._PANELS:
        if key not in core:
            continue
        a = m.addAction(f"{tip}\t{sc}")
        a.setCheckable(True)
        a.setChecked(win._panel_visible.get(key, True))
        a.toggled.connect(lambda on, k=key: win._panel_btns[k].setChecked(on))
    m.addSeparator()
    m.addAction("Command palette\tCtrl+K", win._open_command_palette)


# --- Go (REMOVED) -----------------------------------------------------------------------------
# The Go menu (one 'Open …' per tool + 'New chart window') was dropped: every tool now has a
# title-bar launcher ICON (incl. Journal + Alerts), New chart window has its icon + Ctrl+N, and the
# Ctrl+K palette still lists everything. The menu was a pure duplicate of the icon row.


# --- Insert -----------------------------------------------------------------------------------

def _fill_insert(m, win):
    m.addAction("Indicator…\tƒx", lambda: _active_chart(win)._open_indicator_picker())
    m.addAction("New chart window\tCtrl+N", lambda: win._open_in_new_chart(win._symbol))


# --- Format -----------------------------------------------------------------------------------

def _fill_format(m, win):
    style = _menu(m, "Chart style")
    for _sec, styles in chart_styles.STYLE_SECTIONS:
        style.addSection(_sec)
        for st in styles:
            style.addAction(style_icon(st), st, lambda s=st: _active_chart(win).set_style(s))
    m.addMenu(style)


# --- Window -----------------------------------------------------------------------------------

def _fill_window(m, win):
    """MultiCharts-16 Window menu: arrange verbs, close/detach, numbered open windows."""
    m.addAction("Arrange All", lambda: win._arrange_chart_windows("grid"))
    m.addAction("Arrange Horizontally", lambda: win._arrange_chart_windows("rows"))
    m.addAction("Arrange Vertically", lambda: win._arrange_chart_windows("columns"))
    m.addAction("Cascade", lambda: win._arrange_chart_windows("cascade"))
    m.addSeparator()
    m.addAction("Close Window\tCtrl+F4", win._close_active_window)
    m.addAction("Detach Window", win._float_current_document)
    m.addSeparator()
    active = getattr(win, "_active_frame", None)
    for n, d in enumerate(win._doc_widgets, 1):
        a = m.addAction(f"{n} {d.title()}", lambda doc=d: win._activate_document(doc))
        a.setCheckable(True)
        a.setChecked(active is not None and getattr(active, "doc", None) is d)


# --- Help -------------------------------------------------------------------------------------

def _fill_help(m, win):
    m.addAction("About vike-trader", win._show_about)
