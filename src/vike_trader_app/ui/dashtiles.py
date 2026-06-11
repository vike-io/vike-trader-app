"""Dashboard info tiles: small dockable widgets (Top movers / P&L / Today's calendar / News).

Each tile is a thin, render-only panel — data arrives via its ``set_*``/``merge_*`` slot from
MainWindow wiring (main thread; the data layer is not thread-safe). Tiles are registered as
ADS panel docks like Market watch / Trades, so they drag / float / pin / auto-hide and their
layout persists through the session — arrange some tiles and save a named workspace to get a
personal "dashboard".
"""

from __future__ import annotations

import time

from PySide6 import QtCore, QtWidgets

from . import theme
from .dashtiles_data import age_label, latest_headlines, pnl_summary, top_movers

_CAPTION_QSS = "color:{c};font-size:9px;letter-spacing:.5px;border:none;"
_EMPTY_QSS = f"color:{theme.TEXT3};font-size:11px;border:none;padding:10px;"


class _TileBody(QtWidgets.QWidget):
    """Shared scaffold: a vertical stack of rows over a dim empty-state label."""

    def __init__(self, empty_text: str, parent=None):
        super().__init__(parent)
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(8, 6, 8, 6)
        self._lay.setSpacing(2)
        self._empty = QtWidgets.QLabel(empty_text)
        self._empty.setStyleSheet(_EMPTY_QSS)
        self._empty.setWordWrap(True)
        self._lay.addWidget(self._empty)
        self._lay.addStretch(1)
        self._rows: list[QtWidgets.QWidget] = []

    def _clear_rows(self) -> None:
        for w in self._rows:
            self._lay.removeWidget(w)
            w.deleteLater()
        self._rows.clear()

    def _add_row(self, widget: QtWidgets.QWidget) -> None:
        self._lay.insertWidget(self._lay.count() - 1, widget)  # keep the stretch last
        self._rows.append(widget)

    def _show_empty(self, on: bool) -> None:
        self._empty.setVisible(on)


class MoversTile(_TileBody):
    """Top movers by 24h change, fed from the same quote stream as the watchlist."""

    def __init__(self, parent=None):
        super().__init__("Waiting for quotes…", parent)
        self._prices: dict = {}

    def merge_prices(self, prices: dict) -> None:
        """Fold a quote chunk (symbol -> (last, chg_frac)) in and re-render."""
        self._prices.update({k: v for k, v in prices.items() if v})
        self._render()

    def _render(self) -> None:
        self._clear_rows()
        rows = top_movers(self._prices)
        self._show_empty(not rows)
        for sym, last, chg in rows:
            w = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(w)
            h.setContentsMargins(2, 1, 2, 1)
            name = QtWidgets.QLabel(sym)
            name.setStyleSheet(
                f"color:{theme.TEXT};font-size:11px;font-weight:600;border:none;")
            px = QtWidgets.QLabel(f"{last:,.4g}")
            px.setStyleSheet(
                f"color:{theme.TEXT2};font-family:{theme.FONT_MONO};font-size:11px;border:none;")
            color = theme.UP if chg >= 0 else theme.DOWN
            pct = QtWidgets.QLabel(f"{chg * 100:+.2f}%")
            pct.setStyleSheet(
                f"color:{color};font-family:{theme.FONT_MONO};font-size:11px;"
                f"font-weight:700;border:none;")
            h.addWidget(name)
            h.addStretch(1)
            h.addWidget(px)
            h.addSpacing(10)
            h.addWidget(pct)
            self._add_row(w)


class PnLTile(_TileBody):
    """Account snapshot (equity / P&L / return) from the current backtest or forward run."""

    def __init__(self, parent=None):
        super().__init__("Run a backtest (or start the forward tester) to see P&L.", parent)

    def set_result(self, equity_curve, final_equity: float | None = None) -> None:
        self._clear_rows()
        s = pnl_summary(equity_curve, final_equity)
        self._show_empty(s is None)
        if s is None:
            return
        color = theme.UP if s["pnl"] >= 0 else theme.DOWN
        for cap, text, css in (
            ("EQUITY", f"${s['equity']:,.2f}",
             f"color:{theme.TEXT};font-family:{theme.FONT_MONO};font-size:16px;"
             f"font-weight:700;border:none;"),
            ("P&L", f"{'+' if s['pnl'] >= 0 else '−'}${abs(s['pnl']):,.2f}",
             f"color:{color};font-family:{theme.FONT_MONO};font-size:13px;"
             f"font-weight:700;border:none;"),
            ("RETURN", f"{s['ret_pct']:+.2f}%",
             f"color:{color};font-family:{theme.FONT_MONO};font-size:13px;"
             f"font-weight:700;border:none;"),
        ):
            cell = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(cell)
            v.setContentsMargins(2, 2, 2, 2)
            v.setSpacing(1)
            c = QtWidgets.QLabel(cap)
            c.setStyleSheet(_CAPTION_QSS.format(c=theme.TEXT3))
            val = QtWidgets.QLabel(text)
            val.setStyleSheet(css)
            v.addWidget(c)
            v.addWidget(val)
            self._add_row(cell)


class CalendarTile(_TileBody):
    """Today's economic events from the local calendar cache (no network of its own)."""

    def __init__(self, parent=None):
        super().__init__("No cached events for today — open the Calendar space to fetch.",
                         parent)

    def set_events(self, events) -> None:
        self._clear_rows()
        self._show_empty(not events)
        for ev in events:
            w = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(w)
            h.setContentsMargins(2, 1, 2, 1)
            hh = time.strftime("%H:%M", time.gmtime(ev.ts_utc / 1000))
            t = QtWidgets.QLabel("All day" if ev.all_day else hh)
            t.setStyleSheet(
                f"color:{theme.TEXT3};font-family:{theme.FONT_MONO};font-size:10px;border:none;")
            t.setFixedWidth(44)
            dot_color = {2: theme.DOWN, 1: "#d29922"}.get(ev.importance, theme.TEXT3)
            dot = QtWidgets.QLabel("●")
            dot.setStyleSheet(f"color:{dot_color};font-size:9px;border:none;")
            cur = QtWidgets.QLabel(ev.currency)
            cur.setStyleSheet(f"color:{theme.TEXT2};font-size:10px;font-weight:700;border:none;")
            cur.setFixedWidth(30)
            title = QtWidgets.QLabel(ev.title)
            title.setStyleSheet(f"color:{theme.TEXT};font-size:11px;border:none;")
            title.setToolTip(ev.title)
            h.addWidget(t)
            h.addWidget(dot)
            h.addWidget(cur)
            h.addWidget(title, 1)
            self._add_row(w)


class NewsTile(_TileBody):
    """Latest headlines, mirroring the News space's in-memory feed."""

    def __init__(self, parent=None):
        super().__init__("Open this tile with the news feed running to see headlines.", parent)

    def set_items(self, items) -> None:
        self._clear_rows()
        rows = latest_headlines(items)
        self._show_empty(not rows)
        now_ms = int(time.time() * 1000)
        for it in rows:
            w = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(w)
            h.setContentsMargins(2, 1, 2, 1)
            age = QtWidgets.QLabel(age_label(it.published_ms, now_ms))
            age.setStyleSheet(
                f"color:{theme.TEXT3};font-family:{theme.FONT_MONO};font-size:10px;border:none;")
            age.setFixedWidth(30)
            title = QtWidgets.QLabel(it.title)
            title.setStyleSheet(f"color:{theme.TEXT};font-size:11px;border:none;")
            title.setToolTip(f"{it.source} — {it.title}")
            src = QtWidgets.QLabel(it.source)
            src.setStyleSheet(f"color:{theme.TEXT3};font-size:10px;border:none;")
            h.addWidget(age)
            h.addWidget(title, 1)
            h.addWidget(src)
            self._add_row(w)
