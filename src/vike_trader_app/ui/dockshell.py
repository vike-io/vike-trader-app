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
from PySide6 import QtCore, QtGui, QtWidgets

from . import theme


class VikeDockTitleBar(QtAds.CDockAreaTitleBar):
    """Custom ADS dock-area title bar (unified title bar, stage 1).

    ALWAYS calls super().__init__(area) so ADS's built-in dock-area behaviour keeps its C++
    wiring — we render our own UnifiedTitleBar over the row and hide the native chrome.

    Two flavours, both pixel-identical UnifiedTitleBar chrome ([icon] NAME … ⧉ ─ □ ✕):
      * the central SPACES area -> mark_as_chart_header(deck): a single live MC-style title
        (CHART · BTCUSDT · 5m), buttons wired to float-space / win-min / win-max / close-doc.
      * a side PANEL area (objectName 'panel:…') -> mark_as_panel(): title from the dock,
        buttons wired to ADS undock / auto-hide / float-max / close.
    NOT a tab strip (rejected design)."""

    # Class-level defaults so these attrs ALWAYS exist. Qt's C++ base (CDockAreaTitleBar) can
    # fire resizeEvent DURING super().__init__() — before the instance assignments below run —
    # which calls our resizeEvent override and touches self._header. Without these defaults that
    # early resize raises AttributeError ("'VikeDockTitleBar' object has no attribute '_header'"),
    # a real-platform crash on restore when a space is floated. Offscreen event timing doesn't
    # deliver that construction-time resize, so the GUI suite stayed green — only a live launch hit it.
    _deck = None
    _header = None
    _is_panel = False
    _area_w = None

    def __init__(self, area):
        super().__init__(area)
        self._deck = None
        self._header = None   # UnifiedTitleBar (chart-space header OR a unified panel bar)
        self._is_panel = False
        self._area_w = area
        # Panels self-detect (covers creation AND restoreState recreations); the central
        # spaces area is marked explicitly by SpaceDeck.mark_as_chart_header before this fires.
        # Tie the one-shot to `self`: if ADS destroys this title bar before it fires, the timer
        # is cancelled instead of calling into a dead C++ object (segfault on teardown/fast-close).
        QtCore.QTimer.singleShot(0, self, self._auto_detect_panel)

    def is_chart_header(self) -> bool:
        return self._header is not None

    def mark_as_chart_header(self, deck) -> None:
        """Render the single-title chart-space header into this bar (idempotent — the title
        text is refreshed separately via set_header_title)."""
        from .unifiedbar import UnifiedTitleBar
        from .style_icons import style_icon

        if self._header is not None:
            self.refresh_native_hidden()
            return
        self._deck = deck
        win = None
        try:
            win = self.dockManager().window()
        except (RuntimeError, AttributeError):
            pass

        def _floating_container():
            """The CFloatingDockContainer this chart header lives in, or None when docked in the
            main window. Lets the ─/□ buttons target the FLOAT (not the main window) when the
            chart space has been torn out."""
            try:
                d = deck.dock(max(0, deck.currentIndex()))
            except (RuntimeError, AttributeError, IndexError):
                return None
            if d is None:
                return None
            try:
                if d.isFloating():
                    return d.floatingDockContainer()
            except (RuntimeError, AttributeError):
                return None
            return None

        def _win_min():
            c = _floating_container()
            if c is not None:                      # floated chart space -> minimize the float
                try:
                    c.showMinimized()
                except RuntimeError:
                    pass
            elif win is not None:                  # docked -> minimize the main window
                win.showMinimized()

        def _win_max():
            c = _floating_container()
            if c is not None:                      # floated chart space -> max/restore the float
                try:
                    c.showNormal() if c.isMaximized() else c.showMaximized()
                except RuntimeError:
                    pass
                return
            tb = getattr(win, "titlebar", None)
            if tb is not None and hasattr(tb, "_toggle_max"):
                tb._toggle_max()
            elif win is not None:
                win.showNormal() if win.isMaximized() else win.showMaximized()

        def _detach_or_redock():
            """⧉ — float the chart space if docked, or re-dock it if already floating."""
            idx = max(0, deck.currentIndex())
            try:
                d = deck.dock(idx)
            except (RuntimeError, AttributeError, IndexError):
                d = None
            floating = False
            try:
                floating = d is not None and d.isFloating()
            except (RuntimeError, AttributeError):
                floating = False
            if floating:
                try:
                    d.dockManager().addDockWidget(QtAds.CenterDockWidgetArea, d)
                except (RuntimeError, AttributeError):
                    pass
            else:
                deck.float_space(idx)

        bar = UnifiedTitleBar(title=getattr(deck, "_header_title", "Chart"),
                              icon=style_icon("Candles", theme.ACCENT).pixmap(16, 16),
                              parent=self)
        if win is not None and hasattr(win, "_open_central_as_window"):
            bar.add_button("clone", "＋", "Open this chart in a new window",
                           win._open_central_as_window)
        bar.add_button("detach", "⧉", "Detach / re-dock this space", _detach_or_redock)
        bar.add_button("min", "─", "Minimize", _win_min)
        bar.add_button("max", "□", "Maximize / restore", _win_max)
        bar.add_button("close", "✕", "Close the current chart",
                       deck.close_current_document, danger=True)
        bar.set_active(True)
        if getattr(deck, "_status_provider", None) is not None:   # header link dots (● / ◆)
            deck._status_provider(bar)
        self._header = bar
        # Grow the header to fill the row; the MainWindow then caps its max width to the
        # right-panel's left edge (the dock area itself extends BEHIND the panels), so the
        # ⧉ ─ □ ✕ land at the visible chart's right edge — never under the watchlist.
        self._install_header_widget(bar)
        self.refresh_native_hidden()
        deck._request_fit()

    def set_header_title(self, text: str) -> None:
        if self._header is not None:
            self._header.set_title(text)

    def set_header_title_rich(self, html: str) -> None:
        if self._header is not None:
            self._header.set_title_rich(html)

    def set_header_icon(self, pixmap) -> None:
        if self._header is not None:
            self._header.set_icon(pixmap)

    def _install_header_widget(self, bar) -> None:
        """Insert the UnifiedTitleBar into this title bar's row, expanded to fill it. Shared by
        the chart-space header and the unified panel bars (the chart header is then capped to
        the panel edge by the MainWindow; panel bars fill their own narrow area)."""
        bar.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        try:
            self.insertWidget(0, bar)
        except (RuntimeError, TypeError):
            self.layout().insertWidget(0, bar)
        try:
            self.layout().setStretchFactor(bar, 1)
        except (RuntimeError, TypeError, AttributeError):
            pass

    # Native chrome we suppress even in the MULTI-dock (tabbed) branch: the ▼ tabs-menu button and
    # the green auto-hide pin. The standalone eliding title label (objectName 'autoHideTitleLabel',
    # a DIRECT child of this title bar) is hidden separately — it's a redundant copy of the active
    # tab's title. The tab labels themselves (also CElidingLabel, but children of a CDockWidgetTab)
    # are KEPT so the tab strip stays readable + switchable.
    _MULTI_HIDE_OBJNAMES = ("tabsMenuButton", "dockAreaAutoHideButton")

    def refresh_native_hidden(self) -> None:
        """Suppress the native ADS title-bar chrome that fights our unified bar.

        SINGLE-dock area: hide everything native (the eliding label + all buttons) and show the
        unified bar — one clean custom title. MULTI-dock (tabbed) area: a single custom title can't
        represent N tabs, so HIDE the unified bar and instead show the native (themed) TAB STRIP so
        the user can switch tabs — but still kill the ▼ tabs-menu button, the green auto-hide pin,
        and the redundant standalone eliding title label. KEEP: the switchable tab strip + detach +
        minimize + close. Re-called from resizeEvent / event(LayoutRequest) so it survives tab
        add/remove + relayouts."""
        if self._header is None:
            return
        try:
            multi = self._area_w.dockWidgetsCount() > 1
        except (RuntimeError, AttributeError):
            multi = False
        try:
            self._header.setVisible(not multi)
            for child in self.findChildren(QtWidgets.QWidget):
                if child is self._header or self._header.isAncestorOf(child):
                    continue
                if multi:
                    # keep the switchable tab strip + detach/min/close; drop the ▼ menu + green pin
                    # and the standalone eliding title label (a DIRECT child of THIS title bar — not
                    # the per-tab labels, whose parent is a CDockWidgetTab, which stay visible).
                    hidden = child.objectName() in self._MULTI_HIDE_OBJNAMES or (
                        isinstance(child, QtAds.CElidingLabel) and child.parent() is self
                    )
                    child.setVisible(not hidden)
                else:
                    child.hide()
        except (RuntimeError, AttributeError):
            pass

    def resizeEvent(self, ev):  # noqa: N802 - Qt override
        super().resizeEvent(ev)
        if self._header is not None:        # keep native chrome suppressed across relayouts
            self.refresh_native_hidden()

    def event(self, ev):  # noqa: N802 - Qt override
        res = super().event(ev)
        # ADS re-shows its native buttons (the ▼ tabs-menu, detach, auto-hide, close) when a 2nd
        # dock is TABBED into this area — which does NOT fire a resizeEvent, so the natives leak in
        # next to our unified ⧉ ─ □ ✕. Re-suppress on any layout change (cheap; guarded).
        try:
            if ev.type() == QtCore.QEvent.Type.LayoutRequest and self._header is not None:
                self.refresh_native_hidden()
        except RuntimeError:   # title bar mid-teardown
            pass
        return res

    # --- unified PANEL bar (Market watch / Trades / …) ------------------------------------

    def _auto_detect_panel(self) -> None:
        """A panel area (objectName 'panel:…') gets the SAME UnifiedTitleBar as the chart
        header, for pixel-identical chrome. The central spaces area is excluded (it carries
        'space:'/document docks and is marked as the chart header by SpaceDeck)."""
        if self._header is not None or self._is_panel:
            return
        area = self._area_w
        try:
            n = area.dockWidgetsCount()
        except (RuntimeError, AttributeError):
            return
        for i in range(n):
            try:
                dw = area.dockWidget(i)
            except (RuntimeError, AttributeError):
                continue
            if dw is not None and dw.objectName().startswith(("panel:", "tool:")):
                self.mark_as_panel()    # tool docks get the SAME unified bar as panels — no native
                return                  # ADS chrome (the stray ▼ tabs-menu + duplicate close icon)

    def mark_as_panel(self) -> None:
        """[icon] NAME … ⧉ ─ □ ✕ wired to ADS undock / auto-hide / float-max / close —
        replacing the native tab + buttons (incl. the odd green auto-hide pin)."""
        if self._header is not None:
            return
        from .unifiedbar import UnifiedTitleBar

        self._is_panel = True
        bar = UnifiedTitleBar(parent=self)
        bar.add_button("detach", "⧉", "Detach / re-dock", self._panel_detach)
        bar.add_button("min", "─", "Minimize (collapse to edge)", self._panel_min)
        bar.add_button("max", "□", "Maximize / restore", self._panel_max)
        bar.add_button("close", "✕", "Close", self._panel_close, danger=True)
        self._header = bar
        self._install_header_widget(bar)
        self.refresh_native_hidden()
        # ADS re-shows the native tab/buttons after its deferred relayout — re-hide next turn
        # (tied to self so a destroyed title bar cancels it rather than crashing)
        QtCore.QTimer.singleShot(0, self, self.refresh_native_hidden)
        self._sync_panel_title()
        # Connect THIS instance's slot once (mark_as_panel is guarded by `if self._header`).
        # ADS recreates the area's title bar on relayout; the OLD instance is destroyed, which
        # auto-removes its connection — so no disconnect bookkeeping is needed (and a manual
        # disconnect of this fresh instance's slot just spams "Failed to disconnect" warnings).
        try:
            self._area_w.currentChanged.connect(self._sync_panel_title)
        except (RuntimeError, AttributeError):
            pass

    def _cur_dock(self):
        try:
            return self._area_w.currentDockWidget()
        except (RuntimeError, AttributeError):
            return None

    def _sync_panel_title(self, *_) -> None:
        if self._header is None:
            return
        d = self._cur_dock()
        if d is None:
            return
        self._header.set_title(d.windowTitle())
        try:
            self._header.set_icon(d.icon().pixmap(16, 16))
        except (RuntimeError, AttributeError):
            pass
        self.refresh_native_hidden()

    def _panel_detach(self) -> None:
        """⧉ — float a docked panel, or RE-DOCK a floating one back into the centre."""
        d = self._cur_dock()
        if d is None:
            return
        if d.isFloating():
            try:
                d.dockManager().addDockWidget(QtAds.CenterDockWidgetArea, d)
            except (RuntimeError, AttributeError):
                pass
        else:
            d.setFloating()

    def _panel_min(self) -> None:
        """─ — on a FLOAT, minimize the floating container; otherwise collapse to the edge."""
        d = self._cur_dock()
        if d is None:
            return
        if d.isFloating():
            c = d.floatingDockContainer()
            if c is not None:
                c.showMinimized()
        else:
            d.toggleAutoHide()

    def _panel_max(self) -> None:
        d = self._cur_dock()
        if d is None:
            return
        if not d.isFloating():
            d.setFloating()
        c = d.floatingDockContainer()
        if c is not None:
            c.showNormal() if c.isMaximized() else c.showMaximized()

    def _panel_close(self) -> None:
        d = self._cur_dock()
        if d is not None:
            d.closeDockWidget()


class VikeComponentsFactory(QtAds.CDockComponentsFactory):
    """Installed per-manager (mgr.setComponentsFactory) so offscreen tests / any future
    manager keep ADS defaults. Verified working on PySide6-QtAds 4.5.0.5."""

    def createDockAreaTitleBar(self, area):  # noqa: N802 - ADS naming
        return VikeDockTitleBar(area)


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
    # Frameless floats (custom-float-frames branch): no native OS frame, so we control the chrome.
    QtAds.CDockManager.setConfigFlag(
        QtAds.CDockManager.FloatingContainerForceNativeTitleBar, False)


def make_panel_dock(manager, title: str, widget, area,
                    icon: "QtGui.QIcon | None" = None) -> "QtAds.CDockWidget":
    """An unlockable side-panel dock: closable, draggable, floatable (tear-out to a second
    monitor) and pinnable (the auto-hide edge tabs — AmiBroker-style collapsed panels), with the
    MC-style title bar (icon + detach/min/max/close)."""
    dock = QtAds.CDockWidget(manager, title)
    dock.setObjectName(f"panel:{title}")
    dock.setWidget(widget, QtAds.CDockWidget.ForceNoScrollArea)
    dock.setFeatures(
        QtAds.CDockWidget.DefaultDockWidgetFeatures | QtAds.CDockWidget.DockWidgetPinnable
    )
    manager.addDockWidget(area, dock)
    # The unified panel title bar (VikeDockTitleBar.mark_as_panel) renders [icon] NAME ⧉ ─ □ ✕;
    # just give the dock its icon — NOT apply_mc_titlebar's min/max title-actions, which would
    # render alongside ours as duplicate buttons.
    if icon is not None:
        dock.setIcon(icon)
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
        # ADS recreates the dock-area title bar on relayout, so the chart-space header is
        # STATELESS (state lives here in the model): a freshly-created header re-reads this.
        self._header_title = "Chart"
        self._fit_cb = None   # MainWindow-supplied: cap the header to the panel's left edge
        self._status_provider = None   # MainWindow-supplied: add the header's link dots

    def _resolve_area(self):
        """The spaces' CENTRAL CDockAreaWidget. CDockManager.restoreState() can REBUILD the
        layout into a fresh area object, so the cached reference is re-resolved on every access
        — and the currentChanged forward re-wired when it moved.

        Resolve from the first space dock that is NOT floating/closed: a launcher can pull ANY
        single space (Chart included) into its own floating window, and keying the central area
        to _docks[0] would then follow Chart into the float and strand every other space's
        navigation. Skipping floated/closed docks keeps the central area = where the still-
        docked spaces live; fall back to the cached area only if every space is floating."""
        if not self._docks:
            return self._area
        area = None
        for dock in self._docks:
            try:
                if dock.isFloating() or dock.isClosed():
                    continue
            except RuntimeError:   # dock mid-teardown during restore — skip
                continue
            a = dock.dockAreaWidget()
            if a is not None:
                area = a
                break
        if area is None:
            area = self._area          # all spaces floating/closed -> the cached central area
        elif area is not self._area:
            if self._area is not None:
                try:
                    self._area.currentChanged.disconnect(self._emit_current)
                except (RuntimeError, TypeError):  # old area torn down / not connected
                    pass
            area.currentChanged.connect(self._emit_current)
            self._area = area
            tb = area.titleBar()
            if hasattr(tb, "mark_as_chart_header"):   # VikeDockTitleBar (factory installed)
                tb.mark_as_chart_header(self)
        # restoreState / float relayouts can leave self._area pointing at a C++-DELETED area; probe
        # liveness so every caller gets None (they all guard `area is None`) instead of crashing on
        # the stale ref (e.g. _hide_space_tabs_now / _header_bar / currentIndex calling area.X()).
        if self._area is not None:
            try:
                self._area.objectName()        # cheap; RuntimeError if the C++ object is gone
            except RuntimeError:
                self._area = None
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
        self.hide_space_tabs()   # adding a document rebuilds the tab bar -> re-shows space tabs
        return dock

    def _on_document_closed(self, dock, widget) -> None:
        if dock in self._documents:
            self._documents.remove(dock)
        self.documentClosed.emit(widget)
        self.hide_space_tabs()   # closing one rebuilds the tab bar too

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

    def close_current_document(self) -> None:
        """The ✕ on the chart-space header. If a tear-out chart DOCUMENT is current, close it;
        otherwise close (HIDE) the Chart space itself so the workspace can be fully emptied. The
        Chart dock is hidden, not destroyed — its chart (self.price) survives and the pipeline
        keeps running; the Chart rail/menu launcher re-shows it via show_space()."""
        area = self._resolve_area()
        cur = area.currentDockWidget() if area is not None else None
        if cur in self._documents:
            cur.closeDockWidget()
        elif self._docks:
            self._docks[0].toggleView(False)   # hide the Chart space -> empty workspace

    def show_space(self, index: int = 0) -> None:
        """Re-show a hidden space (the Chart) and make it current — the launcher counterpart of
        the header ✕ that hides it."""
        if 0 <= index < len(self._docks):
            d = self._docks[index]
            if d.isClosed():
                d.toggleView(True)
            d.setAsCurrentTab()
            self.setCurrentIndex(index)

    # --- chart-space header (forwarded to the central area's VikeDockTitleBar) ------------

    def _header_bar(self):
        try:
            area = self._resolve_area()
            if area is None:
                return None
            tb = area.titleBar()
        except RuntimeError:   # restoreState can leave a stale (C++-deleted) CDockAreaWidget ref
            return None
        return tb if hasattr(tb, "set_header_title") else None

    def header_widget(self):
        """The current chart-space header (UnifiedTitleBar) the MainWindow caps to fit."""
        tb = self._header_bar()
        return getattr(tb, "_header", None) if tb is not None else None

    def set_fit_callback(self, fn) -> None:
        self._fit_cb = fn

    def set_header_status_provider(self, fn) -> None:
        """fn(unified_bar) populates the chart-space header's status cluster (link dots). Called
        on every header (re)creation so the dots survive ADS relayouts."""
        self._status_provider = fn

    def _request_fit(self) -> None:
        """Ask the MainWindow to re-cap the header width once the layout settles (the header
        was just (re)created by ADS; geometry isn't final until the next event-loop turn)."""
        if self._fit_cb is not None:
            QtCore.QTimer.singleShot(0, self, self._fit_cb)   # tied to the deck's lifetime

    def set_header_title(self, text: str) -> None:
        self._header_title = text   # remembered so a recreated header re-shows it
        tb = self._header_bar()
        if tb is not None:
            tb.set_header_title(text)

    def set_header_title_rich(self, html: str) -> None:
        tb = self._header_bar()
        if tb is not None:
            tb.set_header_title_rich(html)

    def set_header_icon(self, pixmap) -> None:
        tb = self._header_bar()
        if tb is not None:
            tb.set_header_icon(pixmap)

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
        # The left rail is the space switcher — center tabs for SPACES would just duplicate it,
        # so they're hidden: the strip shows ONLY chart documents (their drag handle for
        # tiling/tear-out). MC-style separation: window tabs ≠ workspace switching.
        dock.tabWidget().setVisible(False)
        return dock

    def hide_space_tabs(self) -> None:
        """Re-hide the space tabs. ADS re-shows them whenever the area's tab bar is rebuilt
        (restoreState, document add/close, current-tab changes), so this runs at every mutation
        point — immediately AND once more on the next event-loop turn to catch ADS's deferred
        relayouts."""
        self._hide_space_tabs_now()
        # tied to self: a destroyed SpaceDeck cancels the deferred call instead of running it
        # on a torn-down object (the segfault class fixed across the title-bar timers)
        QtCore.QTimer.singleShot(0, self, self._hide_space_tabs_now)

    def _hide_space_tabs_now(self) -> None:
        for dock in self._docks:
            try:
                dock.tabWidget().setVisible(False)
            except RuntimeError:   # a tab widget mid-rebuild during restore — skip
                pass
        # Unified title bar: the central area's title bar is now WANTED chrome (it hosts the
        # single-title chart header). Keep it VISIBLE; only re-hide the native tab strip +
        # native buttons the header replaces, which ADS re-shows on relayout.
        area = self._resolve_area()
        if area is not None:
            tb = area.titleBar()
            if hasattr(tb, "refresh_native_hidden"):
                tb.refresh_native_hidden()

    def dock(self, index: int) -> "QtAds.CDockWidget":
        """The CDockWidget wrapping space ``index`` (Phase 2+ uses this directly)."""
        return self._docks[index]

    # MC16-style "window-type launcher": a space opens as a floating OS window (native title
    # bar via FloatingContainerForceNativeTitleBar) over the workspace, like chart windows.
    _FLOAT_FEATURES = (
        QtAds.CDockWidget.DockWidgetMovable | QtAds.CDockWidget.DockWidgetFloatable
        | QtAds.CDockWidget.DockWidgetClosable | QtAds.CDockWidget.DockWidgetFocusable
    )

    def float_space(self, index: int) -> "QtAds.CDockWidget":
        """Open space ``index`` as a floating window; if it already floats, focus it.
        Closing the window just hides the dock — the next launch re-shows it floating."""
        dock = self._docks[index]
        if dock.isClosed():
            dock.toggleView(True)     # re-show (ADS restores its last floating container)
        if not dock.isFloating():
            # pinned spaces carry NoDockWidgetFeatures; grant float-capable features so the
            # floating container is movable/closable like a real window
            dock.setFeatures(self._FLOAT_FEATURES)
            dock.setFloating()
        container = dock.floatingDockContainer()
        if container is not None:
            if container.size().width() < 700:       # first float: give it a usable size
                container.resize(1000, 640)
            container.raise_()
            container.activateWindow()
        dock.setAsCurrentTab()
        # setFloating() rebuilds the central area's tab bar, which ADS re-shows — reclaim the
        # spaces strip again (every other mutation point does this; float_space must too).
        self.hide_space_tabs()
        return dock

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
        try:
            area = self._resolve_area()
            cur = area.currentDockWidget() if area is not None else None
        except RuntimeError:   # area C++ object deleted by a restoreState/float relayout
            return -1
        return self._docks.index(cur) if cur in self._docks else -1

    def currentWidget(self):  # noqa: N802
        try:
            area = self._resolve_area()
            dock = area.currentDockWidget() if area is not None else None
        except RuntimeError:   # stale (deleted) area after a relayout
            return None
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
        if not (0 <= index < len(self._docks)):
            return
        dock = self._docks[index]
        # Float-aware nav: a space launched as a floating window isn't in the central area, so
        # a plain setAsCurrentTab() only re-tabs inside its own float and reads as a dead no-op
        # to the Go menu / palette / Data→Studio handoff. Raise its window instead (and re-show
        # if its window was closed) so every navigation surface can reach a floated space.
        try:
            floated = dock.isFloating() or dock.isClosed()
        except RuntimeError:
            floated = False
        if floated:
            self.float_space(index)
            return
        dock.setAsCurrentTab()
        self.hide_space_tabs()   # becoming current re-shows the tab; keep spaces rail-only

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
    ads--CDockWidgetTab[activeTab="true"] {{ border-bottom: 1px solid {theme.ACCENT}; }}
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
