"""Bots panel (TradeLocker-style): Active Bots / Historic Runs + a Launch Bot button.

Wraps the existing StrategyPanel (the active bot's name + params) and HistoryPanel
(persisted past runs) in a tabbed panel with a prominent Launch Bot action. Holds no
run logic — it emits ``launchRequested`` and re-exposes ``runChosen`` for the host
window to wire to the engine + run store.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from . import theme
from .panels import HistoryPanel, StrategyPanel


class BotsPanel(QtWidgets.QWidget):
    launchRequested = QtCore.Signal()
    runChosen = QtCore.Signal(object)  # forwarded from HistoryPanel

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.strategy = StrategyPanel()
        self.history = HistoryPanel()
        self.history.runChosen.connect(self.runChosen)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tabs = QtWidgets.QTabWidget()
        self._tabs.addTab(self.strategy, "Active Bots")
        self._tabs.addTab(self.history, "Historic Runs")
        root.addWidget(self._tabs, 1)

        bar = QtWidgets.QWidget()
        bar.setStyleSheet(f"border-top:1px solid {theme.BORDER};")
        brow = QtWidgets.QHBoxLayout(bar)
        brow.setContentsMargins(8, 8, 8, 8)
        self.launch_btn = QtWidgets.QPushButton("🚀 Launch Bot")
        self.launch_btn.setObjectName("play")
        self.launch_btn.clicked.connect(self.launchRequested)
        brow.addWidget(self.launch_btn)
        root.addWidget(bar)

    def show_strategy(self, cls) -> None:
        """Show the active bot (delegates to the embedded StrategyPanel)."""
        self.strategy.show_strategy(cls)

    def update_runs(self, runs) -> None:
        """Refresh the Historic Runs list (delegates to the embedded HistoryPanel)."""
        self.history.update_runs(runs)
