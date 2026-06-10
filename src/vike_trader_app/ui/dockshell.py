"""ADS dockable shell: the SPACES live as tabs of a Qt-Advanced-Docking-System center area,
side panels become real dock widgets (drag / float / pin-to-edge auto-hide).

``SpaceDeck`` is a QTabWidget-compatible facade over the center area, so the rest of the
shell (rail wiring, ``_on_tab_changed``, session restore, tests) keeps speaking the
``addTab``/``currentIndex`` vocabulary it always has. Space docks are pinned in place —
not closable/movable/floatable (multi-instance chart documents unlock those in Phase 2) —
so the area's tab order IS the creation order and rail indices stay stable.
"""

from __future__ import annotations

import PySide6QtAds as QtAds
from PySide6 import QtCore

from . import theme


def configure_dock_manager_defaults() -> None:
    """Static CDockManager config — must run BEFORE the manager is instantiated."""
    QtAds.CDockManager.setConfigFlag(QtAds.CDockManager.OpaqueSplitterResize, True)
    # buttons that don't apply (close/undock on the pinned spaces area) hide instead of
    # rendering disabled — keeps the spaces tab row clean
    QtAds.CDockManager.setConfigFlag(QtAds.CDockManager.DockAreaHideDisabledButtons, True)
    QtAds.CDockManager.setAutoHideConfigFlags(QtAds.CDockManager.DefaultAutoHideConfig)


def make_panel_dock(manager, title: str, widget, area) -> "QtAds.CDockWidget":
    """An unlockable side-panel dock: closable, draggable, floatable (tear-out to a second
    monitor) and pinnable (the auto-hide edge tabs — AmiBroker-style collapsed panels)."""
    dock = QtAds.CDockWidget(manager, title)
    dock.setObjectName(f"panel:{title}")
    dock.setWidget(widget, QtAds.CDockWidget.ForceNoScrollArea)
    dock.setFeatures(
        QtAds.CDockWidget.DefaultDockWidgetFeatures | QtAds.CDockWidget.DockWidgetPinnable
    )
    manager.addDockWidget(area, dock)
    return dock


class SpaceDeck(QtCore.QObject):
    """QTabWidget-compatible facade over the ADS center dock area hosting the spaces."""

    currentChanged = QtCore.Signal(int)

    def __init__(self, manager: "QtAds.CDockManager"):
        super().__init__(manager)
        self._mgr = manager
        self._area = None              # the one center CDockAreaWidget (created on first add)
        self._docks: list[QtAds.CDockWidget] = []

    def _resolve_area(self):
        """The spaces' current CDockAreaWidget. CDockManager.restoreState() can REBUILD the
        layout into a fresh area object, so the cached reference is re-resolved from the first
        space dock on every access — and the currentChanged forward re-wired when it moved."""
        if not self._docks:
            return self._area
        area = self._docks[0].dockAreaWidget()
        if area is not None and area is not self._area:
            if self._area is not None:
                try:
                    self._area.currentChanged.disconnect(self._emit_current)
                except Exception:  # noqa: BLE001 - old area may already be torn down
                    pass
            area.currentChanged.connect(self._emit_current)
            self._area = area
        return self._area

    def _emit_current(self, *_):
        """Forward the area's current-tab change as a SPACE index. We map through ``_docks``
        rather than trusting the raw area index: if a foreign dock were ever tabbed into the
        spaces area, the raw index would no longer line up with the rail / _RAIL_ITEMS — this
        keeps the shell driven only by real space indices (and never by a stray panel tab)."""
        idx = self.currentIndex()
        if idx >= 0:
            self.currentChanged.emit(idx)

    # --- construction -------------------------------------------------------------------

    def addTab(self, widget, title: str) -> "QtAds.CDockWidget":
        dock = QtAds.CDockWidget(self._mgr, title)
        dock.setObjectName(f"space:{title}")   # stable id for CDockManager.saveState()
        dock.setWidget(widget, QtAds.CDockWidget.ForceNoScrollArea)
        dock.setFeatures(QtAds.CDockWidget.NoDockWidgetFeatures)  # pinned: tab order == rail order
        # Re-resolve the live target area on every add (not the cached self._area): a prior
        # restoreState can replace the area object, leaving the cache stale — which would bite
        # Phase 2, where chart documents are added at runtime AFTER a session restore.
        # (A central-widget area would refuse foreign drops, but ADS clears that flag the moment
        # a 2nd dock tabs in, so it gives no protection with N tabbed spaces — the
        # _on_tab_changed re-entrancy guard in MainWindow is what prevents the drop-recursion
        # crash instead.)
        target = self._resolve_area() if self._docks else None
        self._mgr.addDockWidget(QtAds.CenterDockWidgetArea, dock, target)
        self._docks.append(dock)
        self._resolve_area()
        return dock

    def dock(self, index: int) -> "QtAds.CDockWidget":
        """The CDockWidget wrapping space ``index`` (Phase 2+ uses this directly)."""
        return self._docks[index]

    # --- QTabWidget vocabulary ------------------------------------------------------------

    def count(self) -> int:
        return len(self._docks)

    def widget(self, index: int):
        return self._docks[index].widget() if 0 <= index < len(self._docks) else None

    def indexOf(self, widget) -> int:  # noqa: N802 - QTabWidget casing
        for i, dock in enumerate(self._docks):
            if dock.widget() is widget:
                return i
        return -1

    def tabText(self, index: int) -> str:  # noqa: N802
        return self._docks[index].windowTitle()

    def currentIndex(self) -> int:  # noqa: N802
        """Index into the SPACES (``_docks``) of the current dock — robust to any foreign dock
        that may have landed in the area (returns -1 if the current dock isn't a space)."""
        area = self._resolve_area()
        cur = area.currentDockWidget() if area is not None else None
        return self._docks.index(cur) if cur in self._docks else -1

    def currentWidget(self):  # noqa: N802
        area = self._resolve_area()
        if area is None:
            return None
        dock = area.currentDockWidget()
        return dock.widget() if dock is not None and dock in self._docks else None

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        # Target the dock WIDGET, not a raw area index — robust if the area's tab order ever
        # diverges from creation order (a stray foreign tab); also re-resolves the area first.
        self._resolve_area()
        if 0 <= index < len(self._docks):
            self._docks[index].setAsCurrentTab()

    def setCurrentWidget(self, widget) -> None:  # noqa: N802
        index = self.indexOf(widget)
        if index >= 0:
            self.setCurrentIndex(index)

    def isAncestorOf(self, widget) -> bool:  # noqa: N802
        return any(dock.isAncestorOf(widget) for dock in self._docks)

    def setVisible(self, on: bool) -> None:  # noqa: N802 - hides the whole spaces area
        area = self._resolve_area()
        if area is not None:
            area.setVisible(on)

    def isVisible(self) -> bool:  # noqa: N802
        area = self._resolve_area()
        return area is not None and area.isVisible()


def dock_qss() -> str:
    """Stylesheet for the ADS chrome (tab row, title bars, auto-hide side bars, splitters),
    built from the existing theme constants so the dock shell reads as native vike UI."""
    return f"""
    ads--CDockContainerWidget {{ background: {theme.BG}; }}
    ads--CDockContainerWidget > QSplitter {{ padding: 0; }}
    ads--CDockSplitter::handle {{ background: {theme.BORDER}; }}
    ads--CDockAreaWidget {{ background: {theme.BG}; border: none; }}

    ads--CDockAreaTitleBar {{
        background: {theme.BG};
        border-bottom: 1px solid {theme.BORDER};
        padding: 0; min-height: 30px;
    }}
    ads--CDockWidgetTab {{
        background: transparent; border: none;
        border-bottom: 2px solid transparent;
        padding: 0 4px;
    }}
    ads--CDockWidgetTab QLabel {{ color: {theme.TEXT3}; font-size: 12px; font-weight: 600; }}
    ads--CDockWidgetTab:hover QLabel {{ color: {theme.TEXT2}; }}
    ads--CDockWidgetTab[activeTab="true"] {{ border-bottom: 2px solid {theme.ACCENT}; }}
    ads--CDockWidgetTab[activeTab="true"] QLabel {{ color: {theme.TEXT}; }}

    ads--CDockWidget {{ background: {theme.BG}; border: none; }}
    ads--CDockWidget > QWidget {{ background: {theme.BG}; }}

    ads--CTitleBarButton {{ background: transparent; border: none; padding: 2px; }}
    ads--CTitleBarButton:hover {{ background: {theme.HOVER}; border-radius: 4px; }}
    #tabsMenuButton, #detachGroupButton, #dockAreaCloseButton, #dockAreaAutoHideButton {{
        background: transparent; border: none;
    }}
    #tabsMenuButton:hover, #detachGroupButton:hover, #dockAreaCloseButton:hover,
    #dockAreaAutoHideButton:hover {{ background: {theme.HOVER}; border-radius: 4px; }}

    ads--CAutoHideSideBar {{ background: {theme.BG}; border: none; }}
    ads--CAutoHideTab {{
        background: transparent; border: none;
        color: {theme.TEXT3}; font-size: 11px; font-weight: 600;
        padding: 6px 2px;
    }}
    ads--CAutoHideTab:hover {{ color: {theme.TEXT}; }}
    ads--CAutoHideDockContainer {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; }}
    ads--CResizeHandle {{ background: {theme.BORDER}; }}

    ads--CFloatingDockContainer {{ background: {theme.BG}; border: 1px solid {theme.BORDER}; }}
    """
