"""ADS dockable shell: the SPACES live as tabs of a Qt-Advanced-Docking-System center area,
side panels become real dock widgets (drag / float / pin-to-edge auto-hide).

``SpaceDeck`` is a QTabWidget-compatible facade over the center area, so the rest of the
shell (rail wiring, ``_on_tab_changed``, session restore, tests) keeps speaking the
``addTab``/``currentIndex`` vocabulary it always has. Space docks are pinned in place —
not closable/movable/floatable (multi-instance chart documents unlock those in Phase 2) —
so the area's tab order IS the creation order and rail indices stay stable.
"""

from __future__ import annotations

import math

import PySide6QtAds as QtAds
from PySide6 import QtCore, QtWidgets

from . import theme


def configure_dock_manager_defaults() -> None:
    """Static CDockManager config — must run BEFORE the manager is instantiated."""
    QtAds.CDockManager.setConfigFlag(QtAds.CDockManager.OpaqueSplitterResize, True)
    # buttons that don't apply (close/undock on the pinned spaces area) hide instead of
    # rendering disabled — keeps the spaces tab row clean
    QtAds.CDockManager.setConfigFlag(QtAds.CDockManager.DockAreaHideDisabledButtons, True)
    QtAds.CDockManager.setAutoHideConfigFlags(QtAds.CDockManager.DefaultAutoHideConfig)
    # --- per-window chrome (MultiCharts-16 parity; see the shell-ux research note) ---
    # the focused dock area is visibly highlighted — MC's colored active title bar
    QtAds.CDockManager.setConfigFlag(QtAds.CDockManager.FocusHighlighting, True)
    # middle-click closes a closable tab (chart documents; no-op on the pinned spaces)
    QtAds.CDockManager.setConfigFlag(QtAds.CDockManager.MiddleMouseButtonClosesTab, True)
    # double-click a tab detaches it to a floating window (MC's "Detach", one gesture)
    QtAds.CDockManager.setConfigFlag(QtAds.CDockManager.DoubleClickUndocksWidget, True)
    # splitting an area for a new document divides the space evenly (clean 2x2 tiling)
    QtAds.CDockManager.setConfigFlag(QtAds.CDockManager.EqualSplitOnInsertion, True)
    # floating windows carry the floated widget's own title (e.g. "BTCUSDT · 1h")
    QtAds.CDockManager.setConfigFlag(QtAds.CDockManager.FloatingContainerHasWidgetTitle, True)


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
    """QTabWidget-compatible facade over the ADS center dock area hosting the spaces.

    Two kinds of tab live in the center area: the fixed SPACES (``_docks``, pinned, rail-driven,
    creation-order == rail index) and runtime chart DOCUMENTS (``_documents``, closable / movable
    / floatable / tear-out — Phase 2). The facade's index/space API is keyed to ``_docks`` only,
    so a document being current reports index -1 (no rail space active); documents have their own
    add/close/list API and the ``documentClosed`` signal.
    """

    currentChanged = QtCore.Signal(int)        # SPACE index (>=0), or -1 when a document/none
    documentClosed = QtCore.Signal(object)     # the ChartDocument widget that was closed

    # Tear-out-capable chart documents: closable, draggable, floatable to a separate window,
    # pinnable to an edge, and destroyed (with their content) when closed.
    _DOC_FEATURES = (
        QtAds.CDockWidget.DockWidgetClosable | QtAds.CDockWidget.DockWidgetMovable
        | QtAds.CDockWidget.DockWidgetFloatable | QtAds.CDockWidget.DockWidgetPinnable
        | QtAds.CDockWidget.DockWidgetFocusable | QtAds.CDockWidget.DockWidgetDeleteOnClose
    )

    def __init__(self, manager: "QtAds.CDockManager"):
        super().__init__(manager)
        self._mgr = manager
        self._area = None              # the one center CDockAreaWidget (created on first add)
        self._docks: list[QtAds.CDockWidget] = []
        self._documents: list[QtAds.CDockWidget] = []

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
        """Forward the area's current-tab change. We map through ``_docks`` rather than trusting
        the raw area index: a document (or any non-space dock) tabbed into the area reports -1,
        so the shell is driven only by real space indices and never by a stray tab. -1 is still
        emitted (unlike before) so the shell can react to a document becoming current (title,
        panel visibility) — consumers must tolerate index -1."""
        self.currentChanged.emit(self.currentIndex())

    # --- chart documents (runtime, tear-out) ---------------------------------------------

    def add_document(self, widget, title: str, object_name: str) -> "QtAds.CDockWidget":
        """Add a runtime chart document tab (closable / floatable / pinnable). ``object_name``
        must be stable for CDockManager.saveState()/restoreState() to map it across a session."""
        dock = QtAds.CDockWidget(self._mgr, title)
        dock.setObjectName(object_name)
        dock.setWidget(widget, QtAds.CDockWidget.ForceNoScrollArea)
        dock.setFeatures(self._DOC_FEATURES)
        self._mgr.addDockWidget(QtAds.CenterDockWidgetArea, dock, self._resolve_area())
        self._documents.append(dock)
        # DeleteOnClose -> the dock is torn down on close; capture the inner widget now so the
        # documentClosed consumer (LiveHub unregister, manifest update) gets a live reference.
        inner = widget
        dock.closed.connect(lambda d=dock, w=inner: self._on_document_closed(d, w))
        # tear-out notification (duck-typed): lets the document show float-only chrome like
        # the keep-on-top pin when it becomes a separate window
        if hasattr(widget, "set_floating"):
            dock.topLevelChanged.connect(widget.set_floating)
        dock.setAsCurrentTab()
        self._resolve_area()
        return dock

    def _on_document_closed(self, dock, widget) -> None:
        if dock in self._documents:
            self._documents.remove(dock)
        self.documentClosed.emit(widget)

    def documents(self) -> list:
        """Live chart-document widgets, in tab order (for session save)."""
        return [d.widget() for d in self._documents if d.widget() is not None]

    def close_all_documents(self) -> None:
        """Close every chart document (DeleteOnClose -> each fires documentClosed for cleanup).
        Used when switching workspaces, which replaces the open-document set wholesale."""
        for dock in list(self._documents):
            dock.closeDockWidget()

    def document_count(self) -> int:
        return len(self._documents)

    def is_document(self, widget) -> bool:
        return any(d.widget() is widget for d in self._documents)

    # --- arrange (MultiCharts Window->Arrange parity, docking-native) ---------------------

    ARRANGE_MODES = ("grid", "columns", "rows", "tabs")

    def arrange_documents(self, mode: str = "grid") -> int:
        """Tile the open chart documents into the centre. Floating documents are pulled back
        in first (every doc is re-tabbed into the centre area, which normalises the splitter
        tree so the splits below are deterministic).

        ``grid``    near-square 2D tiling (the AmiBroker 4-chart wall, for any N)
        ``columns`` one row of side-by-side charts
        ``rows``    one column of stacked charts
        ``tabs``    gather everything back into the centre tab stack (the inverse)

        Cascade is deliberately absent: it is an MDI concept with no meaning in a docking
        shell. Returns the number of documents arranged."""
        docks = [d for d in self._documents if d.widget() is not None]
        if not docks:
            return 0
        base = self._resolve_area()
        for dock in docks:
            was_floating = dock.isFloating()
            self._mgr.addDockWidget(QtAds.CenterDockWidgetArea, dock, base)
            # programmatic re-dock does NOT emit topLevelChanged — sync float-only chrome
            inner = dock.widget()
            if was_floating and hasattr(inner, "set_floating"):
                inner.set_floating(False)
        n = len(docks)
        if mode == "tabs" or n == 1:
            docks[0].setAsCurrentTab()
            return n
        cols = 1 if mode == "rows" else (n if mode == "columns" else math.ceil(math.sqrt(n)))
        # Top row first: split each new column off the previous top cell (Right), then stack
        # the remaining docs under their column tops (Bottom) — the #103 2x2 pattern, for any N.
        tops = [docks[0]]
        for c in range(1, min(cols, n)):
            self._mgr.addDockWidget(QtAds.RightDockWidgetArea, docks[c],
                                    tops[-1].dockAreaWidget())
            tops.append(docks[c])
        above = list(tops)
        for i in range(cols, n):
            c = i % cols
            self._mgr.addDockWidget(QtAds.BottomDockWidgetArea, docks[i],
                                    above[c].dockAreaWidget())
            above[c] = docks[i]
        self._equalize_splitters([d.dockAreaWidget() for d in docks])
        docks[0].setAsCurrentTab()
        return n

    def _equalize_splitters(self, areas: list) -> None:
        """Best-effort equal cell sizes after a tiling: successive ADS splits halve the
        remaining space (1/2, 1/4, ...), so walk the splitters above the arranged areas and
        equalise each one — but ONLY where every child holds an arranged area, which skips
        the root splitter carrying the side panels (Market watch etc.) untouched."""
        splitters: dict[int, QtWidgets.QSplitter] = {}
        for area in areas:
            p = area.parentWidget()
            while p is not None:
                if isinstance(p, QtWidgets.QSplitter):
                    splitters[id(p)] = p
                p = p.parentWidget()
        for s in splitters.values():
            kids = [s.widget(i) for i in range(s.count())]
            if len(kids) < 2 or not all(
                any(k is a or k.isAncestorOf(a) for a in areas) for k in kids
            ):
                continue
            total = s.width() if s.orientation() == QtCore.Qt.Horizontal else s.height()
            if total > 0:
                s.setSizes([max(1, total // len(kids))] * len(kids))

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
        if dock is None:
            return None
        # spaces AND documents share the center area; return either's widget so the shell can
        # tell which is current (currentIndex still reports -1 for a document).
        if dock in self._docks or dock in self._documents:
            return dock.widget()
        return None

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
