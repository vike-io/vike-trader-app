"""Live Positions & Open-Orders panel (armed-hub read-model -> two tables + per-order Cancel).

THIN widget (mirrors ui/order_ticket.py): it renders PanelRows that MainWindow feeds it from the armed
hub on every bus event and emits cancelRequested(coid) from a per-row Cancel button. It holds NO exec
logic and NO hub reference — MainWindow owns the hub, the armed-gate, the confirm, and hub.cancel_ticket.
Empty + Cancel-disabled when not armed.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

_POS_HEADERS = ["Symbol", "Side", "Size", "Avg Px", "Mark", "uPnL"]
_ORD_HEADERS = ["Symbol", "Side", "Type", "Qty", "Filled", "Price", "Status", ""]


class PositionsPanel(QtWidgets.QWidget):
    cancelRequested = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._armed = False
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tabs = QtWidgets.QTabWidget()
        self._pos = self._make_table(_POS_HEADERS)
        self._ord = self._make_table(_ORD_HEADERS)
        self._tabs.addTab(self._pos, "Positions")
        self._tabs.addTab(self._ord, "Open Orders")
        root.addWidget(self._tabs, 1)

    @staticmethod
    def _make_table(headers: list[str]) -> QtWidgets.QTableWidget:
        t = QtWidgets.QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setAlternatingRowColors(True)
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        t.horizontalHeader().setStretchLastSection(False)
        t.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        return t

    # ---- public API ----
    def set_armed(self, armed: bool) -> None:
        self._armed = armed
        if not armed:
            self._pos.setRowCount(0)
            self._ord.setRowCount(0)
        else:
            # re-enable any existing Cancel buttons (rows arrive via the next set_rows)
            self._set_cancel_enabled(True)

    def set_rows(self, rows) -> None:
        self._fill_positions(rows.positions)
        self._fill_orders(rows.orders)

    # ---- internals ----
    def _fill_positions(self, positions) -> None:
        self._pos.setRowCount(len(positions))
        for r, p in enumerate(positions):
            # CRITIC FIX #5: use position_side from the projector (BOTH/LONG/SHORT)
            # rather than re-deriving from sign of size
            side = p.position_side
            mark = "—" if p.mark is None else f"{p.mark:,.2f}"
            cells = [p.symbol, side, f"{p.size:g}", f"{p.avg_px:,.2f}", mark,
                     f"{p.unrealized_pnl:+,.2f}"]
            for c, val in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(val)
                if c == 5:  # uPnL colour by sign (QtCore.Qt.green/red — no theme import needed)
                    item.setForeground(QtCore.Qt.green if p.unrealized_pnl >= 0 else QtCore.Qt.red)
                self._pos.setItem(r, c, item)

    def _fill_orders(self, orders) -> None:
        self._ord.setRowCount(len(orders))
        for r, o in enumerate(orders):
            side = "BUY" if o.side > 0 else "SELL"
            price = "MKT" if o.price is None else f"{o.price:,.2f}"
            cells = [o.symbol, side, o.order_type, f"{o.qty:g}", f"{o.filled_qty:g}",
                     price, o.status]
            for c, val in enumerate(cells):
                self._ord.setItem(r, c, QtWidgets.QTableWidgetItem(val))
            btn = QtWidgets.QPushButton("Cancel")
            btn.setEnabled(self._armed)
            # bind the coid per row (default-arg capture, not late-binding closure)
            btn.clicked.connect(lambda _=False, coid=o.client_order_id: self.cancelRequested.emit(coid))
            self._ord.setCellWidget(r, len(_ORD_HEADERS) - 1, btn)

    def _set_cancel_enabled(self, on: bool) -> None:
        for r in range(self._ord.rowCount()):
            w = self._ord.cellWidget(r, len(_ORD_HEADERS) - 1)
            if isinstance(w, QtWidgets.QPushButton):
                w.setEnabled(on)
