# src/vike_trader_app/ui/live_strategy_bar.py
"""Small live-strategy status control: label + Start/Stop buttons.

Placed near the ExecArmBar in the execution toolbar. Start is disabled unless
armed; Stop is disabled when no strategy is running.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class LiveStrategyBar(QtWidgets.QWidget):
    """Status bar widget for running/stopping a live strategy pump.

    Signals
    -------
    startRequested : Signal()
        Emitted when the user clicks "Run live" (only enabled while armed + not running).
    stopRequested : Signal()
        Emitted when the user clicks "Stop live" (only enabled while running).
    """

    startRequested = QtCore.Signal()
    stopRequested = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self._label = QtWidgets.QLabel("Strategy: —")
        self._btn_start = QtWidgets.QPushButton("▶ Run live")
        self._btn_stop = QtWidgets.QPushButton("■ Stop live")
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(False)
        for w in (self._label, self._btn_start, self._btn_stop):
            lay.addWidget(w)
        self._btn_start.clicked.connect(self.startRequested)
        self._btn_stop.clicked.connect(self.stopRequested)

    def set_armed(self, armed: bool) -> None:
        """Enable/disable Start based on arm state (only active when armed + not running)."""
        running = self._btn_stop.isEnabled()
        self._btn_start.setEnabled(armed and not running)

    def set_running(self, name: str | None) -> None:
        """Update the label and button states. name=None means stopped."""
        if name:
            self._label.setText(f"Strategy: {name}")
            self._btn_start.setEnabled(False)
            self._btn_stop.setEnabled(True)
        else:
            self._label.setText("Strategy: —")
            self._btn_stop.setEnabled(False)
            # Start is re-enabled only if armed — caller must call set_armed again
            self._btn_start.setEnabled(False)
