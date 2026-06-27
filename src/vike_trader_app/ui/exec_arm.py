"""Compact execution-arm control: venue / product / environment / leverage -> an ExecArmSpec.
Holds NO exec logic; the Arm button emits armRequested(spec). MainWindow owns the call site."""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from vike_trader_app.exec.arm_spec import ExecArmSpec, resolve_arm_spec


class ExecArmBar(QtWidgets.QWidget):
    armRequested = QtCore.Signal(object)   # emits ExecArmSpec (built by MainWindow via current_spec)
    disarmRequested = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self._venue = QtWidgets.QComboBox()
        self._venue.addItems(["binance", "bybit", "okx"])
        self._product = QtWidgets.QComboBox()
        self._product.addItems(["Spot", "Perp"])
        self._env = QtWidgets.QComboBox()
        self._env.addItems(["DEMO", "MAINNET"])
        self._leverage = QtWidgets.QSpinBox()
        self._leverage.setRange(1, 125)
        self._leverage.setValue(1)
        self._leverage.setEnabled(False)   # enabled only when product == Perp
        self._arm = QtWidgets.QPushButton("● Arm live (demo)")
        for w in (self._venue, self._product, self._env, self._leverage, self._arm):
            lay.addWidget(w)
        self._product.currentTextChanged.connect(self._on_product_changed)
        self._env.currentTextChanged.connect(self._reflect_env)
        # The Arm button emits None — MainWindow builds the ExecArmSpec with the live symbol.
        self._arm.clicked.connect(self._emit_arm)

    # ------------------------------------------------------------------
    # internal slots
    # ------------------------------------------------------------------

    def _on_product_changed(self, text: str) -> None:
        self._leverage.setEnabled(text == "Perp")

    def _reflect_env(self, text: str) -> None:
        # demo-first: warn-accent MAINNET, never log creds
        self._arm.setText(
            "● ARM LIVE (MAINNET)" if text == "MAINNET" else "● Arm live (demo)"
        )

    def _emit_arm(self) -> None:
        # MainWindow builds the spec with the live symbol — sentinel None triggers the slot.
        self.armRequested.emit(None)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def set_selection(self, *, venue: str, product: str, environment: str, leverage: int) -> None:
        """Restore selectors from saved state. Does NOT emit armRequested (no auto-arm)."""
        self._venue.setCurrentText(venue)
        self._product.setCurrentText(product)
        self._env.setCurrentText(environment)
        self._leverage.setValue(int(leverage))
        self._on_product_changed(product)

    def current_spec(self, symbol: str) -> ExecArmSpec:
        """Build an ExecArmSpec from the current selector state."""
        return resolve_arm_spec(
            venue=self._venue.currentText(),
            environment=self._env.currentText(),
            product=self._product.currentText(),
            symbol=symbol,
            leverage=float(self._leverage.value()),
            env={},
        )
