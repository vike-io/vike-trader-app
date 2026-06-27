"""Compact live order ticket: side / type / qty / price / reduce-only -> submitRequested(dict).

THIN widget (mirrors ExecArmBar): it emits the raw inputs and renders the status/position lines that
MainWindow feeds it. It holds NO exec logic — MainWindow builds the OrderRequest (the Qt-free
exec/order_ticket.build_order_request seam) and reaches the armed hub. Send is disabled unless armed.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from . import theme  # theme lives at ui/theme.py; for a module under ui/ use a relative import


class OrderTicket(QtWidgets.QWidget):
    submitRequested = QtCore.Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._armed = False
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)

        self._buy = QtWidgets.QPushButton("Buy")
        self._sell = QtWidgets.QPushButton("Sell")
        for b in (self._buy, self._sell):
            b.setCheckable(True)
        grp = QtWidgets.QButtonGroup(self)
        grp.setExclusive(True)
        grp.addButton(self._buy)
        grp.addButton(self._sell)
        self._buy.setChecked(True)

        self._type = QtWidgets.QComboBox()
        self._type.addItems(["Market", "Limit"])
        self._qty = QtWidgets.QDoubleSpinBox()
        self._qty.setDecimals(8)
        self._qty.setRange(0.0, 1e9)
        self._price = QtWidgets.QDoubleSpinBox()
        self._price.setDecimals(2)
        self._price.setRange(0.0, 1e12)
        self._price.setVisible(False)
        self._reduce = QtWidgets.QCheckBox("reduce-only")
        self._send = QtWidgets.QPushButton("Send")
        self._send.setEnabled(False)

        self._ctx = QtWidgets.QLabel("")
        self._status = QtWidgets.QLabel("")
        self._position = QtWidgets.QLabel("")

        for w in (self._buy, self._sell, self._type, self._qty, self._price,
                  self._reduce, self._send, self._ctx, self._status, self._position):
            lay.addWidget(w)

        self._type.currentTextChanged.connect(
            lambda t: self._price.setVisible(t == "Limit"))
        self._send.clicked.connect(self._on_send)

    # ---- public API ----
    def set_armed(self, armed: bool, *, venue: str = "", symbol: str = "",
                  environment: str = "") -> None:
        self._armed = armed
        self._send.setEnabled(armed)
        if armed:
            warn = environment == "MAINNET"
            self._ctx.setText(f"armed: {environment} {venue} {symbol}")
            self._ctx.setStyleSheet(
                f"color:{theme.DOWN if warn else theme.UP};background:transparent;border:none;")
        else:
            self._ctx.setText("")
            self._status.setText("")
            self._position.setText("")

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def set_position(self, text: str) -> None:
        self._position.setText(text)

    # ---- internals ----
    def _on_send(self) -> None:
        if not self._armed:
            return
        is_limit = self._type.currentText() == "Limit"
        self.submitRequested.emit({
            "side": 1 if self._buy.isChecked() else -1,
            "qty": float(self._qty.value()),
            "order_type": "limit" if is_limit else "market",
            "price": float(self._price.value()) if is_limit else None,
            "reduce_only": self._reduce.isChecked(),
        })
