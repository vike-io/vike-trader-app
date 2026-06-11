"""Main menu (S3 of the shell-UX plan) — MultiCharts-16-style hamburger cascade.

``build_main_menu(win)`` returns the ≡ menu with File / View / Insert / Format / Window / Help
submenus, each repopulated on ``aboutToShow`` so dynamic lists (workspaces, recents, open
windows) are always current. It is a THIN routing layer over actions MainWindow already has —
no business logic lives here.
"""

from __future__ import annotations

from PySide6 import QtWidgets

from . import chart_styles, theme
from .style_icons import style_icon

_QSS = (
    f"QMenu{{background:{theme.SURFACE};border:1px solid {theme.BORDER};"
    f"border-radius:{theme.RADIUS_POPUP}px;padding:4px;}}"
    f"QMenu::item{{padding:{theme.DROPDOWN_ITEM_PAD};border-radius:{theme.RADIUS_SM}px;"
    f"color:{theme.TEXT2};}}"
    f"QMenu::item:selected{{background:{theme.HOVER};color:{theme.TEXT};}}"
    f"QMenu::separator{{height:1px;background:{theme.BORDER};margin:4px 8px;}}"
)


def _menu(parent, title: str) -> QtWidgets.QMenu:
    m = QtWidgets.QMenu(title, parent)
    m.setStyleSheet(_QSS)
    return m


def build_main_menu(win) -> QtWidgets.QMenu:
    root = _menu(win, "")
    for title, fill in (("File", _fill_file), ("View", _fill_view), ("Insert", _fill_insert),
                        ("Format", _fill_format), ("Window", _fill_window), ("Help", _fill_help)):
        sub = _menu(root, title)
        sub.aboutToShow.connect(lambda s=sub, f=fill: (s.clear(), f(s, win)))
        root.addMenu(sub)
    return root


def _active_chart(win):
    """The chart the Insert/Format verbs target: the focused chart document, else the Chart space."""
    current = win.tabs.currentWidget()
    return current.chart if hasattr(current, "chart") else win.price


# --- File -------------------------------------------------------------------------------------

def _fill_file(m, win):
    new = _menu(m, "New")
    new.addAction("Chart window\tCtrl+N", lambda: win._open_in_new_chart(win._symbol))
    new.addSeparator()
    for i, (_g, name) in enumerate(win._RAIL_ITEMS):
        new.addAction(f"Go to {name}", lambda idx=i: win.tabs.setCurrentIndex(idx))
    m.addMenu(new)
    m.addSeparator()
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
    m.addAction("Exit", win.close)


# --- View -------------------------------------------------------------------------------------

def _fill_view(m, win):
    for key, _icon, tip, sc in win._PANELS:
        a = m.addAction(f"{tip}\t{sc}")
        a.setCheckable(True)
        a.setChecked(win._panel_visible.get(key, True))
        a.toggled.connect(lambda on, k=key: win._panel_btns[k].setChecked(on))
    m.addSeparator()
    m.addAction("Command palette\tCtrl+K", win._open_command_palette)


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
    m.addAction("New chart window\tCtrl+N", lambda: win._open_in_new_chart(win._symbol))
    m.addAction("Copy window\tCtrl+Shift+C", win._copy_active_document)
    m.addAction("Paste window\tCtrl+Shift+V", win._paste_document)
    m.addSeparator()
    m.addAction("Float current document", win._float_current_document)
    arrange = _menu(m, "Arrange chart windows")
    arrange.addAction("Tile grid", lambda: win.tabs.arrange_documents("grid"))
    arrange.addAction("Side by side", lambda: win.tabs.arrange_documents("columns"))
    arrange.addAction("Stacked", lambda: win.tabs.arrange_documents("rows"))
    arrange.addAction("Gather as tabs", lambda: win.tabs.arrange_documents("tabs"))
    m.addMenu(arrange)
    m.addSeparator()
    docs = win.tabs.documents()
    if docs:
        for d in docs:
            m.addAction(d.title(), lambda doc=d: win._activate_document(doc))
        m.addSeparator()
    for i, (_g, name) in enumerate(win._RAIL_ITEMS):
        m.addAction(name, lambda idx=i: win.tabs.setCurrentIndex(idx))


# --- Help -------------------------------------------------------------------------------------

def _fill_help(m, win):
    m.addAction("Keyboard shortcuts", win._show_shortcuts)
    m.addAction("About vike-trader", win._show_about)
