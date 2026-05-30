"""Trades-table row formatting (Qt-free; the Qt widget just renders these rows)."""

from datetime import UTC, datetime

TRADE_HEADERS = ["#", "Entry time", "Entry", "Exit time", "Exit", "Size", "PnL", "Fees"]


def _fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def trade_rows(trades) -> list[list[str]]:
    """Format trades into display rows matching ``TRADE_HEADERS``."""
    rows: list[list[str]] = []
    for i, t in enumerate(trades, start=1):
        rows.append(
            [
                str(i),
                _fmt_ts(t.entry_ts),
                f"{t.entry_price:.2f}",
                _fmt_ts(t.exit_ts),
                f"{t.exit_price:.2f}",
                f"{t.size:.4f}",
                f"{t.pnl:+.2f}",
                f"{t.fees:.2f}",
            ]
        )
    return rows
