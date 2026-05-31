"""Tools tab — standalone crypto calculators (a thin Qt shell over analysis.calculators).

A scrollable grid of self-contained calculator cards (position size, liquidation, funding,
PnL, expectancy, risk-of-ruin). Each card auto-recomputes on any input change. No data/engine
dependency — pure utilities for quick what-ifs, and a low-friction onboarding surface.
"""

from PySide6 import QtCore, QtWidgets

from ..analysis import calculators as C
from . import theme


class _CalcCard(QtWidgets.QGroupBox):
    """One calculator: a titled form of numeric/choice fields + a live result line.

    ``fields`` items: ``(key, label, "num", (default, decimals, min, max))`` or
    ``(key, label, "choice", [options])``. ``compute(values) -> str`` returns the result text.
    """

    def __init__(self, title: str, fields: list, compute):
        super().__init__(title)
        self._compute = compute
        self._fields: dict[str, tuple] = {}
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(12, 16, 12, 12)
        form.setSpacing(7)

        for key, label, kind, spec in fields:
            if kind == "choice":
                w = QtWidgets.QComboBox()
                w.addItems(spec)
                w.currentIndexChanged.connect(self._recompute)
            else:
                default, decimals, lo, hi = spec
                w = QtWidgets.QDoubleSpinBox()
                w.setRange(lo, hi)
                w.setDecimals(decimals)
                w.setValue(default)
                w.setGroupSeparatorShown(True)
                w.valueChanged.connect(self._recompute)
            self._fields[key] = (kind, w)
            form.addRow(label, w)

        self._out = QtWidgets.QLabel()
        self._out.setWordWrap(True)
        self._out.setStyleSheet(
            f"color:{theme.UP};font-weight:700;font-size:12px;"
            f"background:{theme.PANEL2};border:1px solid {theme.BORDER};"
            f"border-radius:6px;padding:7px 9px;"
        )
        form.addRow(self._out)
        self._recompute()

    def values(self) -> dict:
        out = {}
        for key, (kind, w) in self._fields.items():
            out[key] = w.currentText() if kind == "choice" else w.value()
        return out

    def _recompute(self) -> None:
        try:
            self._out.setText(self._compute(self.values()))
        except Exception as exc:  # noqa: BLE001 - never let a calc crash the tab
            self._out.setText(f"— ({exc})")


def _cards() -> list[_CalcCard]:
    money = lambda v: f"${v:,.2f}"  # noqa: E731

    def position(v):
        r = C.position_size(v["account"], v["risk_pct"], v["entry"], v["stop"])
        return f"Qty {r['qty']:,.4f}  ·  Notional {money(r['notional'])}  ·  Risk {money(r['risk_amount'])}"

    def liq(v):
        p = C.liquidation_price(v["entry"], v["leverage"], v["side"], v["maint_pct"])
        return f"Liquidation ≈ {money(p)}"

    def funding(v):
        c = C.funding_cost(v["notional"], v["rate_pct"] / 100.0, v["periods"])
        sign = "pay" if c >= 0 else "receive"
        return f"You {sign} {money(abs(c))} over {v['periods']:.0f} periods"

    def pnl(v):
        r = C.trade_pnl(v["entry"], v["exit"], v["qty"], v["side"], v["fee_pct"] / 100.0)
        return (f"Net {money(r['net'])}  ({r['return_pct']:+.2f}%)  ·  "
                f"gross {money(r['gross'])} − fees {money(r['fees'])}")

    def exp(v):
        r = C.expectancy(v["win_pct"] / 100.0, v["avg_win"], v["avg_loss"])
        pf = "∞" if r["profit_factor"] == float("inf") else f"{r['profit_factor']:.2f}"
        return f"Expectancy {r['expectancy']:+,.4f} / trade  ·  Profit factor {pf}"

    def ror(v):
        p = C.risk_of_ruin(v["win_pct"] / 100.0, v["payoff"], v["risk_pct"],
                           ruin_drawdown_pct=v["ruin_pct"], trials=1500, max_trades=500)
        return f"Risk of ruin ≈ {p * 100:.1f}%  (lose {v['ruin_pct']:.0f}% within 500 trades)"

    return [
        _CalcCard("Position size", [
            ("account", "Account ($)", "num", (10_000.0, 2, 0.0, 1e12)),
            ("risk_pct", "Risk per trade (%)", "num", (1.0, 2, 0.0, 100.0)),
            ("entry", "Entry", "num", (100.0, 4, 0.0, 1e12)),
            ("stop", "Stop", "num", (95.0, 4, 0.0, 1e12)),
        ], position),
        _CalcCard("Liquidation price", [
            ("entry", "Entry", "num", (100.0, 4, 0.0, 1e12)),
            ("leverage", "Leverage (×)", "num", (10.0, 2, 0.0, 125.0)),
            ("side", "Side", "choice", ["long", "short"]),
            ("maint_pct", "Maint. margin (%)", "num", (0.5, 3, 0.0, 100.0)),
        ], liq),
        _CalcCard("Funding cost", [
            ("notional", "Position notional ($)", "num", (10_000.0, 2, 0.0, 1e12)),
            ("rate_pct", "Funding rate (%/period)", "num", (0.01, 4, -5.0, 5.0)),
            ("periods", "Periods held", "num", (3.0, 0, 0.0, 1e6)),
        ], funding),
        _CalcCard("Trade PnL", [
            ("entry", "Entry", "num", (100.0, 4, 0.0, 1e12)),
            ("exit", "Exit", "num", (110.0, 4, 0.0, 1e12)),
            ("qty", "Quantity", "num", (10.0, 4, 0.0, 1e12)),
            ("side", "Side", "choice", ["long", "short"]),
            ("fee_pct", "Fee per side (%)", "num", (0.05, 4, 0.0, 5.0)),
        ], pnl),
        _CalcCard("Expectancy", [
            ("win_pct", "Win rate (%)", "num", (50.0, 2, 0.0, 100.0)),
            ("avg_win", "Avg win ($/R)", "num", (2.0, 4, 0.0, 1e9)),
            ("avg_loss", "Avg loss ($/R)", "num", (1.0, 4, 0.0, 1e9)),
        ], exp),
        _CalcCard("Risk of ruin", [
            ("win_pct", "Win rate (%)", "num", (55.0, 2, 0.0, 100.0)),
            ("payoff", "Payoff ratio (R)", "num", (1.5, 3, 0.0, 100.0)),
            ("risk_pct", "Risk per trade (%)", "num", (2.0, 3, 0.01, 100.0)),
            ("ruin_pct", "Ruin = drawdown of (%)", "num", (100.0, 1, 1.0, 100.0)),
        ], ror),
    ]


class ToolsTab(QtWidgets.QWidget):
    """Scrollable 2-column grid of calculator cards."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        body = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(body)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self.cards = _cards()
        for i, card in enumerate(self.cards):
            grid.addWidget(card, i // 2, i % 2, QtCore.Qt.AlignTop)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch((len(self.cards) + 1) // 2, 1)
        scroll.setWidget(body)
        root.addWidget(scroll)
