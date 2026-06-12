"""MC-style floating chart WINDOWS (S7) — overlapping, per-window title bars, NO docking.

Each chart opens as a ``ChartWindowFrame``: a free-floating frame OVER the workspace (parented
to the dock manager, like MultiCharts' child windows) with its own MC-style title bar —
``[icon] BTCUSDT · 1m … [⊼ pin] [⧉ detach] [─] [□] [✕]`` — draggable by the bar, resizable by
its edges, double-click maximizes, minimize rolls the body up. Detach pops the SAME frame out
to a separate OS window (multi-monitor) and back. The host arranges (cascade / grid / rows /
columns) with plain geometry math — the user explicitly rejected dock-tiling for charts.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .style_icons import style_icon
from .unifiedbar import BAR_H, FeedBadge, UnifiedTitleBar

TITLE_H = BAR_H   # one shared title-bar height across every surface (chart header / panels)
_EDGE = 6          # resize-border thickness (frame edges)
_MIN_W, _MIN_H = 320, 160
_SNAP_M = 28       # drag-to-edge snap zone thickness (attached frames; detached use OS snap)


class ChartWindowFrame(QtWidgets.QFrame):
    """One floating chart window: custom title bar + a ChartDocument body."""

    closed = QtCore.Signal(object)        # self
    activated = QtCore.Signal(object)     # self (clicked/raised)
    cloneRequested = QtCore.Signal(object)   # self — duplicate this window (MainWindow handles)

    def __init__(self, doc, host: QtWidgets.QWidget):
        super().__init__(host)
        self.doc = doc
        self._host = host
        self._maxed = False
        self._rolled = False
        self._normal_geo: QtCore.QRect | None = None
        self._drag_off: QtCore.QPoint | None = None
        self._resize_edge: tuple[bool, bool, bool, bool] | None = None
        self._snap_target: "QtCore.QRect | None" = None   # pending drag-to-edge snap region
        self._snap_overlay: "QtWidgets.QWidget | None" = None
        # Live-resize throttle: each mouse-move only STORES the target geometry (O(1)); the
        # expensive setGeometry → full pyqtgraph relayout (~90ms with 1500 candles) runs at
        # most once per tick, so the cursor never waits on the chart. Without this the handler
        # ran the relayout synchronously per move and the mouse "stuck" (measured 57-98ms/move).
        self._pending_geo: QtCore.QRect | None = None
        self._resize_timer = QtCore.QTimer(self)
        self._resize_timer.setInterval(16)        # ~60fps cap for the chart relayout
        self._resize_timer.timeout.connect(self._flush_resize)
        self.setObjectName("chartWin")
        self.setMouseTracking(True)
        self.setStyleSheet(
            f"#chartWin{{background:{theme.BG};border:1px solid {theme.BORDER};}}"
        )
        # NO QGraphicsDropShadowEffect: it forced a full-window re-render on EVERY move (it
        # made multi-window dragging ~70% slower — measured) for a cosmetic shadow. Detached
        # frames are real OS windows and get the native drop shadow for free; attached frames
        # read fine against the workspace with just the 1px border above.

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)

        # --- title bar (shared chrome — unifiedbar) --------------------------------------
        self._bar = UnifiedTitleBar(
            title=doc.title(),
            icon=style_icon("Candles", theme.ACCENT).pixmap(16, 16))
        # adopt the doc's symbol-link (●) + interval-link (◆) dots into the title bar's status
        # cluster (MC link colours live on the window chrome, not buried in the chart toolbar)
        for _dot in (getattr(doc, "_link_dot", None), getattr(doc, "_ivl_dot", None)):
            if _dot is not None:
                self._bar.add_status(_dot)
        self._feed_badge = FeedBadge()        # per-window data state (set by MainWindow)
        self._bar.add_status(self._feed_badge)
        # adopt the doc's keep-on-top pin (float-only chrome) into the title bar (MC's "stick")
        if getattr(doc, "_pin_btn", None) is not None:
            self._bar.add_widget(doc._pin_btn)
        self._bar.add_button("clone", "＋", "Clone this window",
                             lambda: self.cloneRequested.emit(self))
        self._detach_btn = self._bar.add_button(
            "detach", "⧉", "Detach to its own window", self.toggle_detach)
        self._bar.add_button("min", "─", "Minimize (roll up)", self.toggle_rollup)
        self._max_btn = self._bar.add_button("max", "□", "Maximize", self.toggle_max)
        self._bar.add_button("close", "✕", "Close", self.close_window, danger=True)
        self._bar.set_menu(self._show_menu)
        lay.addWidget(self._bar)
        lay.addWidget(doc, 1)

        if hasattr(doc, "symbolChanged"):
            doc.symbolChanged.connect(lambda *_: self._bar.set_title(doc.title()))

        self._bar.installEventFilter(self)
        self.resize(720, 460)

    # --- window verbs ---------------------------------------------------------------------

    def is_detached(self) -> bool:
        return self.parent() is None

    def toggle_detach(self) -> None:
        if self.is_detached():
            geo = self.geometry()
            self.setParent(self._host)
            self.move(60, 60)
            self.resize(geo.size())
            self.show()
            self._detach_btn.setToolTip("Detach to its own window")
            if hasattr(self.doc, "set_floating"):
                self.doc.set_floating(False)
        else:
            global_pos = self.mapToGlobal(QtCore.QPoint(0, 0))
            self.setParent(None)
            self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
            self.move(global_pos)
            self.show()
            self._detach_btn.setToolTip("Attach back into the workspace")
            if hasattr(self.doc, "set_floating"):
                self.doc.set_floating(True)
        self.raise_()
        self.activated.emit(self)

    def toggle_rollup(self) -> None:
        self._rolled = not self._rolled
        body = self.doc
        body.setVisible(not self._rolled)
        if self._rolled:
            self._roll_geo = self.geometry()
            self.resize(self.width(), TITLE_H + 2)
        else:
            self.resize(self._roll_geo.size())

    def toggle_max(self) -> None:
        if self._rolled:
            self.toggle_rollup()
        if not self._maxed:
            self._normal_geo = self.geometry()
            self._maxed = True
            self._fit_to_host()
            self._max_btn.setText("❐")
            self._max_btn.setToolTip("Restore")
        else:
            self._maxed = False
            if self._normal_geo is not None:
                self.setGeometry(self._normal_geo)
            self._max_btn.setText("□")
            self._max_btn.setToolTip("Maximize")
        self.raise_()

    def _fit_to_host(self) -> None:
        if self.is_detached():
            scr = self.screen().availableGeometry()
            self.setGeometry(scr)
        else:
            self.setGeometry(self._host.rect())

    def host_resized(self) -> None:
        """Called by the host when the workspace resizes — keep maximized frames filling it
        and floating ones inside it."""
        if self._maxed:
            self._fit_to_host()
        elif not self.is_detached():
            r = self._host.rect()
            self.move(min(self.x(), max(0, r.width() - 60)),
                      min(self.y(), max(0, r.height() - TITLE_H)))

    def close_window(self) -> None:
        self.closed.emit(self)
        if self._snap_overlay is not None:   # child of the HOST, not us — delete it explicitly
            self._snap_overlay.deleteLater()
            self._snap_overlay = None
        self.hide()
        self.deleteLater()

    # --- drag / resize / activate -----------------------------------------------------------

    def eventFilter(self, obj, ev):  # noqa: N802 - title-bar drag + double-click maximize
        if obj is self._bar:
            t = ev.type()
            if t == QtCore.QEvent.MouseButtonPress and ev.button() == QtCore.Qt.LeftButton:
                # offset of the cursor inside the window, in GLOBAL pixels (works attached
                # — parent coords — and detached — screen coords — alike)
                self._drag_off = (ev.globalPosition().toPoint()
                                  - self.mapToGlobal(QtCore.QPoint(0, 0)))
                self.raise_()
                self.activated.emit(self)
                return False
            if t == QtCore.QEvent.MouseMove and self._drag_off is not None and not self._maxed:
                top_left_global = ev.globalPosition().toPoint() - self._drag_off
                if self.is_detached():
                    self.move(top_left_global)
                else:
                    target = self._host.mapFromGlobal(top_left_global)
                    r = self._host.rect()   # keep at least the title bar inside the workspace
                    target.setX(max(-self.width() + 80, min(target.x(), r.width() - 80)))
                    target.setY(max(0, min(target.y(), r.height() - TITLE_H)))
                    self.move(target)
                    cursor = self._host.mapFromGlobal(ev.globalPosition().toPoint())
                    self._update_snap_preview(self._snap_zone_rect(cursor))
                return True
            if t == QtCore.QEvent.MouseButtonRelease:
                self._drag_off = None
                if self._snap_target is not None and not self.is_detached():
                    g = QtCore.QRect(self._snap_target)   # commit the drag-to-edge snap
                    g.setWidth(max(g.width(), _MIN_W))    # never snap below the min frame size
                    g.setHeight(max(g.height(), _MIN_H))
                    self.setGeometry(g)
                self._update_snap_preview(None)
                return False
            if t == QtCore.QEvent.MouseButtonDblClick and ev.button() == QtCore.Qt.LeftButton:
                self.toggle_max()
                return True
        return super().eventFilter(obj, ev)

    def mousePressEvent(self, ev):  # noqa: N802 - edge-resize start + activate
        self.raise_()
        self.activated.emit(self)
        if ev.button() == QtCore.Qt.LeftButton and not self._maxed:
            e = self._edge_at(ev.position().toPoint())
            if any(e):
                self._resize_edge = e
                self._press_geo = self.geometry()
                self._press_pos = ev.globalPosition().toPoint()
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):  # noqa: N802 - edge-resize drag + cursor shape
        if self._resize_edge is not None:
            l, t, r, b = self._resize_edge
            d = ev.globalPosition().toPoint() - self._press_pos
            g = QtCore.QRect(self._press_geo)
            if l:
                g.setLeft(min(g.left() + d.x(), g.right() - _MIN_W))
            if r:
                g.setRight(max(g.right() + d.x(), g.left() + _MIN_W))
            if t:
                g.setTop(min(g.top() + d.y(), g.bottom() - _MIN_H))
            if b:
                g.setBottom(max(g.bottom() + d.y(), g.top() + _MIN_H))
            # Throttle: store the target; apply the leading edge immediately, then coalesce
            # further moves to the 16ms tick so the relayout can't starve the mouse queue.
            self._pending_geo = g
            if not self._resize_timer.isActive():
                self.setGeometry(g)
                self._pending_geo = None
                self._resize_timer.start()
            return
        e = self._edge_at(ev.position().toPoint())
        cur = {(1, 0, 0, 0): QtCore.Qt.SizeHorCursor, (0, 0, 1, 0): QtCore.Qt.SizeHorCursor,
               (0, 1, 0, 0): QtCore.Qt.SizeVerCursor, (0, 0, 0, 1): QtCore.Qt.SizeVerCursor,
               (1, 1, 0, 0): QtCore.Qt.SizeFDiagCursor, (0, 0, 1, 1): QtCore.Qt.SizeFDiagCursor,
               (1, 0, 0, 1): QtCore.Qt.SizeBDiagCursor, (0, 1, 1, 0): QtCore.Qt.SizeBDiagCursor,
               }.get(tuple(int(x) for x in e))
        self.setCursor(cur or QtCore.Qt.ArrowCursor)
        super().mouseMoveEvent(ev)

    def _flush_resize(self) -> None:
        """Timer tick during a live resize: apply the latest pending geometry, or stop the
        timer once the user has paused (no pending move since the last tick)."""
        if self._pending_geo is not None:
            self.setGeometry(self._pending_geo)
            self._pending_geo = None
        else:
            self._resize_timer.stop()

    def mouseReleaseEvent(self, ev):  # noqa: N802
        # apply the final geometry exactly (don't lose the last sub-tick move) and stop ticking
        if self._pending_geo is not None:
            self.setGeometry(self._pending_geo)
            self._pending_geo = None
        self._resize_timer.stop()
        self._resize_edge = None
        super().mouseReleaseEvent(ev)

    def _edge_at(self, p: QtCore.QPoint):
        return (p.x() <= _EDGE, p.y() <= _EDGE,
                p.x() >= self.width() - _EDGE, p.y() >= self.height() - _EDGE)

    # --- drag-to-edge snap (attached frames; detached windows use the OS snap) -------------

    def _snap_zone_rect(self, cursor: QtCore.QPoint) -> "QtCore.QRect | None":
        """The target region for a drag that ended with the cursor near a host edge/corner:
        edges → halves, corners → quarters, top edge → full. None when not in a snap zone."""
        r = self._host.rect()
        w, h, m = r.width(), r.height(), _SNAP_M
        if w < 2 * m or h < 2 * m:   # host too small for non-overlapping edge zones — no snap
            return None
        hw, hh = w // 2, h // 2
        near_l, near_r = cursor.x() <= m, cursor.x() >= w - m
        near_t, near_b = cursor.y() <= m, cursor.y() >= h - m
        if near_t and near_l: return QtCore.QRect(0, 0, hw, hh)
        if near_t and near_r: return QtCore.QRect(hw, 0, w - hw, hh)
        if near_b and near_l: return QtCore.QRect(0, hh, hw, h - hh)
        if near_b and near_r: return QtCore.QRect(hw, hh, w - hw, h - hh)
        if near_l: return QtCore.QRect(0, 0, hw, h)
        if near_r: return QtCore.QRect(hw, 0, w - hw, h)
        if near_t: return QtCore.QRect(0, 0, w, h)              # top edge → maximize/full
        return None

    def _update_snap_preview(self, zone: "QtCore.QRect | None") -> None:
        """Show/hide the translucent snap-preview overlay and remember the pending target."""
        self._snap_target = zone
        if zone is None:
            if self._snap_overlay is not None:
                self._snap_overlay.hide()
            return
        if self._snap_overlay is None:
            ov = QtWidgets.QWidget(self._host)
            ov.setObjectName("snapPreview")
            c = QtGui.QColor(theme.ACCENT)
            ov.setStyleSheet(
                f"#snapPreview{{background:rgba({c.red()},{c.green()},{c.blue()},45);"
                f"border:2px solid {theme.ACCENT};}}")
            ov.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            self._snap_overlay = ov
        self._snap_overlay.setGeometry(zone)
        self._snap_overlay.show()
        self._snap_overlay.raise_()

    def set_active(self, on: bool) -> None:
        """The ACTIVE window is shown by its bar background alone — no accent underline
        (the green rule under the header was removed per the user)."""
        self._bar.set_active(on)

    def set_feed(self, color: str, text: str) -> None:
        """Paint this window's feed badge (MainWindow maps the live state -> colour + text)."""
        self._feed_badge.set_state(color, text)

    def _show_menu(self, global_pos) -> None:
        """Title-bar right-click menu (frame-local actions). Plain QMenu → it inherits the app's
        unified popup style; never set a local stylesheet here (the cascade gotcha)."""
        m = QtWidgets.QMenu(self)
        m.addAction("Clone window", lambda: self.cloneRequested.emit(self))
        pin = getattr(self.doc, "_pin_btn", None)
        if pin is not None:
            act = m.addAction("Keep on top", pin.toggle)
            act.setCheckable(True)
            act.setChecked(pin.isChecked())
        m.addAction("Attach back into workspace" if self.is_detached() else "Detach to a window",
                    self.toggle_detach)
        m.addAction("Restore" if self._maxed else "Maximize", self.toggle_max)
        m.addSeparator()
        m.addAction("Close", self.close_window)
        m.exec(global_pos)


def arrange(frames: list[ChartWindowFrame], host: QtWidgets.QWidget, mode: str) -> None:
    """Geometry-math arrangement of the attached frames (no docking): cascade / grid /
    columns / rows. Geometries are computed first, then applied with each frame's painting
    disabled — so the bulk re-tile costs ONE repaint/relayout per frame instead of letting
    intermediate FullViewportUpdate repaints stack up (a cold 4-window grid went ~2.4s→~0.3s)."""
    live = [f for f in frames if not f.is_detached() and not f._rolled]
    if not live:
        return
    r = host.rect()
    n = len(live)
    if mode == "cascade":
        w, h = max(_MIN_W, int(r.width() * 0.55)), max(_MIN_H, int(r.height() * 0.55))
        geos = [QtCore.QRect(24 + i * 36, 16 + i * 30, w, h) for i in range(n)]
    else:
        if mode == "grid":
            import math
            cols = max(1, math.ceil(math.sqrt(n)))
            rows = max(1, math.ceil(n / cols))
        elif mode == "columns":
            cols, rows = n, 1
        else:                   # "rows"
            cols, rows = 1, n
        cw, ch = r.width() // cols, r.height() // rows
        geos = [QtCore.QRect((i % cols) * cw, (i // cols) * ch, cw, ch) for i in range(n)]
    for f, g in zip(live, geos):
        f._maxed = False
        f.setUpdatesEnabled(False)
        f.setGeometry(g)
    for f in live:
        f.setUpdatesEnabled(True)
        f.update()
    if mode == "cascade":
        for f in live:
            f.raise_()
