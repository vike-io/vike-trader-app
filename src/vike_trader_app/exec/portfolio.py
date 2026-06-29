"""Cross-venue Portfolio aggregator — a pure, Qt-free read-model over per-venue Accounts.

The Nautilus structure: ONE ``Account`` per venue (``exec/accounting.py``), aggregated
by a session-scoped ``Portfolio``. The Portfolio owns NO fill logic — callers feed each
``Account`` (``apply_fill`` / ``set_mark`` / ``apply_account_state``); the Portfolio just
sums across them. ``equity()`` is correct in BOTH balance regimes (backtest 'delta' / live
'authoritative') with no caller-side conditional, because each ``Account.equity_all(seed)``
branches on its own ``balance_mode``.

Created Accounts are always ``balance_mode='delta'`` with ``seed`` recorded here; a live
Account flips itself to ``'authoritative'`` inside ``apply_account_state`` on its first
venue balance frame (S1), at which point ``equity_all`` drops the seed/realized terms — so
``equity()`` keeps reading the live venue balance authoritatively with no Portfolio change.
"""

from __future__ import annotations

from vike_trader_app.exec.accounting import Account


class Portfolio:
    """Aggregates per-venue ``Account``s into total equity / exposure / breakdown reads."""

    def __init__(self) -> None:
        self.accounts: dict[str, Account] = {}   # venue -> Account
        self.seeds: dict[str, float] = {}        # venue -> seed (live = 0.0)

    def account(
        self,
        venue: str,
        multipliers: dict[str, float] | None = None,
        seed: float = 0.0,
    ) -> Account:
        """Lazy-create the per-venue Account (balance_mode='delta'); record seed.

        Idempotent: a repeat call for an existing venue returns the SAME Account and
        IGNORES the passed ``multipliers``/``seed`` (the Account binds its multipliers
        once, at creation, to keep hub binding deterministic).
        """
        existing = self.accounts.get(venue)
        if existing is not None:
            return existing
        acc = Account(venue=venue, multipliers=multipliers, balance_mode="delta")
        self.accounts[venue] = acc
        self.seeds[venue] = seed
        return acc

    def equity(self) -> float:
        """Total equity = Σ_v Account.equity_all(seed_v). Correct in both balance modes."""
        return sum(acc.equity_all(self.seeds[v]) for v, acc in self.accounts.items())

    def realized(self) -> float:
        """Gross realized price PnL summed across every venue Account."""
        return sum(acc.realized_pnl for acc in self.accounts.values())

    def unrealized(self) -> float:
        """Mark-to-market PnL summed over every open position across all venues."""
        total = 0.0
        for acc in self.accounts.values():
            for (venue, symbol, side) in acc.positions:
                total += acc.unrealized_pnl(venue, symbol, side)
        return total

    def fees(self) -> float:
        """Cumulative signed commission summed across venues (>0 net cost, <0 net rebate)."""
        return sum(acc.fees_paid for acc in self.accounts.values())

    def funding(self) -> float:
        """Cumulative signed funding cashflow summed across venues."""
        return sum(acc.funding_paid for acc in self.accounts.values())

    def net_position(self, symbol: str) -> float:
        """Signed Σ of position size for ``symbol`` across ALL venues/sides (basis-leg view).

        Multiplier-independent (raw contract size). +2 on one venue and -2 on another cancel.
        """
        total = 0.0
        for acc in self.accounts.values():
            for (venue, sym, side), pos in acc.positions.items():
                if sym == symbol:
                    total += pos["size"]
        return total

    def exposure(self) -> float:
        """Gross notional = Σ abs(size) * mark * multiplier_of(symbol) over all open positions.

        Uses each position's recorded mark; a position with no mark yet contributes 0.0.
        """
        total = 0.0
        for acc in self.accounts.values():
            for (venue, symbol, side), pos in acc.positions.items():
                mark = acc.marks.get((venue, symbol))
                if mark is None:
                    continue
                total += abs(pos["size"]) * mark * acc.multiplier_of(symbol)
        return total

    def venue_breakdown(self) -> dict[str, dict]:
        """Per-venue snapshot for the positions panel.

        {venue: {"equity", "realized", "unrealized", "fees", "funding",
                 "positions": {(symbol, position_side): {"size", "avg_px"}}}}
        Each venue's equity is its own mode-aware ``equity_all(seed_v)``.
        """
        out: dict[str, dict] = {}
        for venue, acc in self.accounts.items():
            unreal = 0.0
            positions: dict[tuple[str, str], dict] = {}
            for (v, symbol, side), pos in acc.positions.items():
                unreal += acc.unrealized_pnl(v, symbol, side)
                positions[(symbol, side)] = {"size": pos["size"], "avg_px": pos["avg_px"]}
            out[venue] = {
                "equity": acc.equity_all(self.seeds[venue]),
                "realized": acc.realized_pnl,
                "unrealized": unreal,
                "fees": acc.fees_paid,
                "funding": acc.funding_paid,
                "positions": positions,
            }
        return out
