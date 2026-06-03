"""Anti-overfit validation dialog: optimize the strategy and show the risk verdict."""

from PySide6 import QtWidgets

from ..analysis.report import build_overfit_report
from . import theme


def show_validation(parent, bars, strategy_cls, fee_rate: float = 0.001) -> None:
    """Run the overfit report for ``strategy_cls`` over ``bars`` and display it."""
    grid = getattr(strategy_cls, "PARAM_GRID", {})
    if not grid:
        QtWidgets.QMessageBox.information(
            parent,
            "Validate",
            f"{strategy_cls.__name__} declares no PARAM_GRID, so there is nothing to "
            "optimize or validate. Add a PARAM_GRID to enable anti-overfit checks.",
        )
        return
    report = build_overfit_report(bars, strategy_cls.make, grid, n_splits=4, fee_rate=fee_rate)
    ValidationDialog(parent, report).exec()


class ValidationDialog(QtWidgets.QDialog):
    """Shows the overfit-risk verdict plus the supporting statistics."""

    def __init__(self, parent, report):
        super().__init__(parent)
        self.setWindowTitle("Anti-overfit validation")
        self.setMinimumWidth(460)
        layout = QtWidgets.QVBoxLayout(self)

        color = theme.VERDICT.get(report.verdict.level, theme.TEXT2)
        head = QtWidgets.QLabel(f"⚠ Overfit risk: {report.verdict.level}")
        head.setStyleSheet(f"font-size:18px; font-weight:700; color:{color};")
        layout.addWidget(head)

        form = QtWidgets.QFormLayout()
        form.addRow("Best params:", QtWidgets.QLabel(str(report.best_params)))
        form.addRow("Best Sharpe (annualized):", QtWidgets.QLabel(f"{report.best_sharpe:.2f}"))
        form.addRow("Deflated Sharpe:", QtWidgets.QLabel(f"{report.deflated_sharpe:.0%}"))
        form.addRow("PBO (overfit prob.):", QtWidgets.QLabel(f"{report.pbo:.0%}"))
        form.addRow("Configurations tried:", QtWidgets.QLabel(str(report.n_trials)))
        layout.addLayout(form)

        layout.addWidget(QtWidgets.QLabel("Why:"))
        for reason in report.verdict.reasons:
            lbl = QtWidgets.QLabel("• " + reason)
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
