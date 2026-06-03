"""Shared dropdown/popup building blocks.

One spec for every dropdown (see docs/research/2026-06-03-dropdown-unification-report.md):

* ``make_search`` / ``section_label`` — the one search field + one section header.
* ``PopupCard`` — frameless, translucent, drop-shadowed rounded card (the indicator
  picker / settings / object-tree share it).
* ``ChecklistPopover`` + ``FilterPill`` — one TradingView-style dark checklist dropdown
  (search + rows + Select-all) driven by a pill trigger, used by the News filters, the
  Calendar category filter (single-select), and the Country picker (multi + flag icons).

Popup surfaces use the unified tokens: ``theme.SURFACE`` bg, ``1px theme.BORDER``,
``theme.RADIUS_POPUP`` radius, ``theme.DROPDOWN_ITEM_PAD`` rows, ``theme.apply_popup_shadow``.
"""

from __future__ import annotations

import time

from PySide6 import QtCore, QtGui, QtWidgets

from . import icons, theme


def make_search(placeholder: str = "Search", *, bg: str = theme.BG) -> QtWidgets.QLineEdit:
    """The single embedded search field for dropdowns: RADIUS_MD, accent focus.

    ``bg`` defaults to the inset BG (for cards/menus on the SURFACE elevation). Pass
    ``theme.SURFACE`` to make the field flush with its SURFACE card so the popup reads as ONE flat
    background (the TradingView checklist look) — the field is then defined only by its border.
    """
    e = QtWidgets.QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setClearButtonEnabled(True)
    e.setStyleSheet(
        f"QLineEdit{{background:{bg};color:{theme.TEXT};border:1px solid {theme.BORDER};"
        f"border-radius:{theme.RADIUS_MD}px;padding:7px 11px;font-size:13px;}}"
        f"QLineEdit:focus{{border-color:{theme.ACCENT};}}"
    )
    return e


def section_label(text: str) -> QtWidgets.QLabel:
    """The single section-header style for grouped dropdowns/lists: tiny uppercase TEXT3 caption."""
    lbl = QtWidgets.QLabel(text.upper())
    lbl.setStyleSheet(
        f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:0.5px;"
        f"background:transparent;border:none;padding:4px 6px 2px;"
    )
    return lbl


class PopupCard(QtWidgets.QDialog):
    """Frameless, translucent, drop-shadowed rounded card — the TradingView floating-panel look.

    Does the boilerplate once: frameless + Dialog flags, ``WA_TranslucentBackground``, a
    translucent root layout whose ``theme.CARD_MARGIN`` margin leaves room for the shadow, and
    an inner ``self.card`` (``QFrame``) carrying the rounded SURFACE background + drop shadow.
    Subclasses give ``self.card`` a layout and fill it, and may pass per-class ``extra_qss``.
    """

    def __init__(self, parent=None, *, object_name: str = "popupCard",
                 radius: int | None = None, extra_qss: str = ""):
        super().__init__(parent)
        radius = theme.RADIUS_POPUP if radius is None else radius
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.Dialog)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(
            f"#{object_name}{{background:{theme.SURFACE};border:1px solid {theme.BORDER};"
            f"border-radius:{radius}px;}}" + extra_qss
        )
        root = QtWidgets.QVBoxLayout(self)  # translucent root; the margin gives the shadow room
        m = theme.CARD_MARGIN
        root.setContentsMargins(m, m, m, m)
        self.card = QtWidgets.QFrame()
        self.card.setObjectName(object_name)
        root.addWidget(self.card)
        theme.apply_popup_shadow(self.card)

    def resize_card(self, w: int, h: int) -> None:
        """Resize to a card of (w, h), adding the shadow margin on every side."""
        m = 2 * theme.CARD_MARGIN
        self.resize(w + m, h + m)


def _norm_options(options) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for o in options:
        if isinstance(o, (tuple, list)):
            out.append((o[0], o[1]))
        else:
            out.append((o, o))
    return out


class ChecklistPopover(QtWidgets.QFrame):
    """One dark checklist dropdown (header + search + rows + optional Select-all).

    ``mode='multi'`` -> independent checkboxes + a tri-state Select-all footer; empty == all.
    ``mode='single'`` -> exclusive (radio-like) checks, no Select-all.
    ``row_icons`` -> optional ``dict[value]`` or ``callable(value)`` returning a QIcon/QPixmap.
    ``header_widgets`` -> optional widgets inserted under the search (e.g. quick-pick chips).
    """

    selectionChanged = QtCore.Signal()
    # OPAQUE, zero-margin popup: the card fills 100% of the popup window, so no BG can show as a
    # dark "box" around it. Translucent-margin + drop-shadow was abandoned because Qt.Popup +
    # WA_TranslucentBackground is unreliable on the Windows compositor — the global
    # `QWidget{background:BG}` painted the margin as a black frame (offscreen render() hid it).
    _MARGIN = 0

    def __init__(self, title: str, options, *, mode: str = "multi", row_icons=None,
                 header_widgets=None, width: int = 224, parent=None):
        super().__init__(parent, QtCore.Qt.Popup)
        self.setObjectName("clpop")
        self._mode = mode
        self._on_hide = None
        self._opts = _norm_options(options)
        self.setStyleSheet(
            # Window frame == the card surface (covers corner triangles left by the card radius).
            f"#clpop{{background:{theme.SURFACE};border-radius:{theme.RADIUS_POPUP}px;}}"
            f"#filterPop{{background:{theme.SURFACE};border:1px solid {theme.BORDER};"
            f"border-radius:{theme.RADIUS_POPUP}px;}}"
            f"#filterPop QLabel#hdr{{color:{theme.TEXT};font-size:14px;font-weight:600;"
            f"background:transparent;border:none;}}"
            # Rows are transparent so they read as the SURFACE card, never the app-wide BG (a
            # backgroundless QCheckBox would otherwise inherit `QWidget{{background:BG}}` and tint
            # the list darker than the card — the "two background colours" defect).
            f"#filterPop QCheckBox{{color:{theme.TEXT2};font-size:14px;spacing:10px;"
            f"padding:6px 6px;border-radius:6px;background:transparent;}}"
            f"#filterPop QCheckBox:hover{{color:{theme.TEXT};background:{theme.HOVER};}}"
            f"#filterPop QCheckBox::indicator{{width:16px;height:16px;border:1px solid {theme.TEXT3};"
            f"border-radius:4px;background:transparent;}}"
            f"#filterPop QCheckBox::indicator:checked{{background:{theme.ACCENT};"
            f"border-color:{theme.ACCENT};}}"
            f"#filterPop QScrollArea{{border:none;background:transparent;}}"
            # The scroll viewport + its #clbody host are plain QWidgets, so the app-wide
            # `QWidget{{background:BG}}` rule would paint the list area a darker BG than the
            # SURFACE card — pin them transparent so the popup reads as one flat surface.
            f"#filterPop QScrollArea > QWidget{{background:transparent;}}"
            f"#filterPop #clbody{{background:transparent;}}"
            f"#filterPop #divider{{background:{theme.BORDER};}}"
        )

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(self._MARGIN, self._MARGIN, self._MARGIN, self._MARGIN)
        card = QtWidgets.QFrame()
        card.setObjectName("filterPop")
        outer.addWidget(card)
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(10, 10, 10, 8)
        v.setSpacing(8)

        hdr = QtWidgets.QLabel(title)
        hdr.setObjectName("hdr")
        v.addWidget(hdr)

        self._search = make_search("Search", bg=theme.SURFACE)  # flush with the card → one flat bg
        self._search.textChanged.connect(self._apply_filter)
        v.addWidget(self._search)

        for w in (header_widgets or []):
            v.addWidget(w)

        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        body = QtWidgets.QWidget()
        body.setObjectName("clbody")
        bl = QtWidgets.QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(1)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(mode == "single")
        self._boxes: dict[str, QtWidgets.QCheckBox] = {}
        for value, label in self._opts:
            cb = QtWidgets.QCheckBox(label)
            cb.setCursor(QtCore.Qt.PointingHandCursor)
            if row_icons is not None:
                ic = row_icons(value) if callable(row_icons) else row_icons.get(value)
                if ic is not None:
                    cb.setIcon(ic if isinstance(ic, QtGui.QIcon) else QtGui.QIcon(ic))
                    cb.setIconSize(QtCore.QSize(16, 16))
            cb.toggled.connect(self._on_toggled)
            self._group.addButton(cb)
            bl.addWidget(cb)
            self._boxes[value] = cb
        bl.addStretch(1)
        area.setWidget(body)
        v.addWidget(area, 1)

        if mode == "multi":
            divider = QtWidgets.QFrame()
            divider.setObjectName("divider")
            divider.setFixedHeight(1)
            v.addWidget(divider)
            self._all = QtWidgets.QCheckBox("Select all")
            self._all.setCursor(QtCore.Qt.PointingHandCursor)
            self._all.clicked.connect(self._on_select_all_clicked)
            v.addWidget(self._all)
        else:
            self._all = None

        card.setFixedWidth(width)
        # Size to content (no scroll for short/medium lists); cap at ~78% of the screen so only
        # very long lists scroll — matching TradingView (whose filter dropdown caps ~65% viewport).
        screen = QtWidgets.QApplication.primaryScreen()
        avail = screen.availableGeometry().height() if screen else 900
        self.setMaximumHeight(int(avail * 0.78) + 2 * self._MARGIN)
        self._sync_select_all()

    # ---- internal ----
    def _apply_filter(self, text: str) -> None:
        t = text.strip().lower()
        for (value, label), cb in zip(self._opts, self._boxes.values()):
            cb.setVisible(not t or t in label.lower() or t in str(value).lower())

    def _on_toggled(self, checked: bool) -> None:
        if self._mode == "single" and not checked:
            return  # ignore the auto-uncheck of the previously-selected row in an exclusive group
        self._sync_select_all()
        self.selectionChanged.emit()

    def _on_select_all_clicked(self) -> None:
        target = not all(cb.isChecked() for cb in self._boxes.values())
        for cb in self._boxes.values():
            cb.blockSignals(True)
            cb.setChecked(target)
            cb.blockSignals(False)
        self._sync_select_all()
        self.selectionChanged.emit()

    def _sync_select_all(self) -> None:
        if self._all is None:
            return
        checked = sum(cb.isChecked() for cb in self._boxes.values())
        self._all.blockSignals(True)
        if checked == 0:
            self._all.setCheckState(QtCore.Qt.Unchecked)
        elif checked == len(self._boxes):
            self._all.setCheckState(QtCore.Qt.Checked)
        else:
            self._all.setCheckState(QtCore.Qt.PartiallyChecked)
        self._all.blockSignals(False)

    def hideEvent(self, e):  # noqa: N802 - Qt override; notify the owner it just closed
        super().hideEvent(e)
        if self._on_hide:
            self._on_hide()

    # ---- multi API (empty == all / no constraint) ----
    def selected(self) -> set[str]:
        return {value for value, cb in self._boxes.items() if cb.isChecked()}

    def set_selected(self, values) -> None:
        wanted = set(values or set())
        for value, cb in self._boxes.items():
            cb.blockSignals(True)
            cb.setChecked(value in wanted)
            cb.blockSignals(False)
        self._sync_select_all()

    # ---- single API ----
    def current(self) -> str | None:
        return next((value for value, cb in self._boxes.items() if cb.isChecked()), None)

    def set_current(self, value) -> None:
        for v, cb in self._boxes.items():
            cb.blockSignals(True)
            cb.setChecked(v == value)
            cb.blockSignals(False)


class FilterPill(QtWidgets.QToolButton):
    """A TradingView-style pill that opens a :class:`ChecklistPopover`.

    ``mode='multi'`` -> label shows "Name (n)"; empty selection == no constraint (all).
    ``mode='single'`` -> label shows the chosen option's text; one row checked at a time.
    A unified thin chevron is drawn at the right (down when closed, up while the popover is open),
    matching every other dropdown/nav arrow in the app (the TradingView look).
    """

    selectionChanged = QtCore.Signal()

    def __init__(self, label: str, options, *, mode: str = "multi", row_icons=None,
                 header_widgets=None, width: int = 224, parent=None):
        super().__init__(parent)
        self._label = label
        self._mode = mode
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        # Right padding reserves room for the chevron drawn in paintEvent (so the label never
        # overlaps it); left/right are otherwise the unified pill insets.
        self.setStyleSheet(
            f"QToolButton{{background:{theme.SURFACE};color:{theme.TEXT2};"
            f"border:1px solid {theme.BORDER};border-radius:{theme.RADIUS_MD}px;"
            f"padding:7px 28px 7px 14px;font-size:13px;text-align:left;}}"
            f"QToolButton:hover{{color:{theme.TEXT};border-color:{theme.TEXT3};}}"
            "QToolButton::menu-indicator{width:0px;}"
        )
        self._pop = ChecklistPopover(label, options, mode=mode, row_icons=row_icons,
                                     header_widgets=header_widgets, width=width, parent=self)
        self._pop.selectionChanged.connect(self._on_changed)
        self._pop._on_hide = self._note_closed
        self._closed_at = 0.0
        self.clicked.connect(self._toggle_pop)
        self._refresh_text()

    def _toggle_pop(self) -> None:
        if self._pop.isVisible():
            self._pop.hide()
            self.update()       # repaint so the chevron flips back down
            return
        # A Qt.Popup grabs the mouse: clicking the open pill first auto-hides it, then this click
        # arrives — ignore a click that lands right after the popover closed itself.
        if time.monotonic() - self._closed_at < 0.20:
            return
        m = self._pop._MARGIN  # offset so the card (inset by its shadow margin) drops under the pill
        self._pop.move(self.mapToGlobal(QtCore.QPoint(-m, self.height() + 4 - m)))
        self._pop.show()
        self.update()           # repaint so the chevron flips up while open

    def _note_closed(self) -> None:
        self._closed_at = time.monotonic()
        self.update()           # popover auto-closed (click outside) -> flip the chevron down

    def _on_changed(self) -> None:
        self._refresh_text()
        self.selectionChanged.emit()

    def _refresh_text(self) -> None:
        if self._mode == "multi":
            n = len(self._pop.selected())
            self.setText(f"{self._label} ({n})" if n else self._label)
        else:
            self.setText(dict(self._pop._opts).get(self._pop.current(), self._label))

    def paintEvent(self, e):  # noqa: N802 - draw the unified chevron at the right (flips on open)
        super().paintEvent(e)   # styled bg/border/label first
        name = "chevron_up" if self._pop.isVisible() else "chevron_down"
        color = theme.TEXT if self.underMouse() else theme.TEXT2
        px = icons.ARROW_PX
        x = self.width() - px - 11
        y = (self.height() - px) / 2
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        p.drawPixmap(QtCore.QRectF(x, y, px, px), icons._pixmap(name, color),
                     QtCore.QRectF(0, 0, icons._S, icons._S))
        p.end()

    # ---- multi API ----
    def selected(self) -> set[str]:
        return self._pop.selected()

    def set_selected(self, values) -> None:
        self._pop.set_selected(values)
        self._refresh_text()
        self.selectionChanged.emit()

    # ---- single API ----
    def current(self) -> str | None:
        return self._pop.current()

    def set_current(self, value) -> None:
        self._pop.set_current(value)
        self._refresh_text()
