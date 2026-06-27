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
        self._armed = False
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self._venue = QtWidgets.QComboBox()
        self._venue.addItems(["binance", "bybit", "okx"])
        self._product = QtWidgets.QComboBox()
        self._product.addItems(["Spot", "Perp"])
        self._env = QtWidgets.QComboBox()
        self._env.addItems(["DEMO", "MAINNET"])
        self._leverage = QtWidgets.QSpinBox()
        self._leverage.setRange(1, 20)     # matches MainWindow._exec_max_leverage() default cap — no
        self._leverage.setValue(1)         # silent clamp: the UI can't request leverage the gate clamps away
        self._leverage.setEnabled(False)   # enabled only when product == Perp (and not armed)
        self._arm = QtWidgets.QPushButton("● Arm live (demo)")
        for w in (self._venue, self._product, self._env, self._leverage, self._arm):
            lay.addWidget(w)
        self._product.currentTextChanged.connect(self._on_product_changed)
        self._env.currentTextChanged.connect(self._reflect_button)
        # Toggle button: idle -> armRequested (MainWindow builds the spec with the live symbol);
        # armed -> disarmRequested (so the teardown path is USER-REACHABLE).
        self._arm.clicked.connect(self._on_click)

    # ------------------------------------------------------------------
    # internal slots
    # ------------------------------------------------------------------

    def _on_product_changed(self, text: str) -> None:
        self._leverage.setEnabled(not self._armed and text == "Perp")

    def _reflect_button(self, *_a) -> None:
        # demo-first: warn-accent MAINNET, never log creds; "Disarm" while armed.
        if self._armed:
            self._arm.setText("■ Disarm")
        else:
            self._arm.setText(
                "● ARM LIVE (MAINNET)" if self._env.currentText() == "MAINNET" else "● Arm live (demo)"
            )

    def _on_click(self) -> None:
        # Sentinel None on arm — MainWindow builds the spec with the live symbol.
        if self._armed:
            self.disarmRequested.emit()
        else:
            self.armRequested.emit(None)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def set_armed(self, armed: bool) -> None:
        """Reflect the live-exec armed state: the button toggles Arm <-> Disarm and the selectors lock
        while armed (changing venue/product/env requires disarming first). MainWindow calls this after a
        successful arm and after a disarm so the (correct) teardown path is USER-REACHABLE."""
        self._armed = armed
        for w in (self._venue, self._product, self._env):
            w.setEnabled(not armed)
        self._leverage.setEnabled(not armed and self._product.currentText() == "Perp")
        self._reflect_button()

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
