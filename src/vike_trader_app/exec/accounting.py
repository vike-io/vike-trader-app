"""FillEvent-derived account read-model: positions + realized PnL from the fill stream.

A pure, Qt-free subscriber. ``apply_fill`` reproduces ``core.single_symbol_engine.SingleSymbolEngine._apply_fill``'s
position branches (open / add-same-direction averaged cost / reduce / close-and-flip) so the realized
PnL on each closing portion — ``(price - avg_px) * (sign * closing) * multiplier`` — equals the
engine's ``Trade.pnl`` exactly (GROSS price PnL). The SIGNED ``FillEvent.commission`` (>0 charge /
<0 maker rebate) is netted into ``balance`` (and tracked in ``fees_paid``), NOT into the gross
``realized_pnl`` — see ``apply_fill``. Positions are keyed ``(venue, symbol, position_side)`` with
``position_side="BOTH"`` for one-way/spot — the tuple reserves the hedge-mode dimension for perps.

S1 contract-A surface:
- per-venue: ``self.venue``; ``apply_fill`` asserts ``fill.venue == self.venue``.
- per-symbol multiplier: ``_mult`` dict + ``multiplier_of(symbol)`` replaces the old scalar
  ``self.multiplier`` (REMOVED); ``_mult_default`` is the legacy scalar (back-compat for oms.py:54
  and the C2 gate which calls ``Account(multiplier=K)``).
- explicit ``balance_mode`` ('delta' | 'authoritative'); ``equity_all(seed)`` is mode-aware;
  ``apply_account_state`` flips the mode after each authoritative balance assignment.
- ``_fold`` is the SOLE writer of position + realized PnL (called by both ``apply_fill`` and
  ``apply_liquidation`` — single compute_fill block, no drift).
- NO equity cache anywhere (catastrophic-cancellation risk).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vike_trader_app.core.fill import compute_fill

if TYPE_CHECKING:
    from vike_trader_app.exec.events import AccountState, FillEvent, FundingEvent, PositionLiquidated


class Account:
    """Folds a stream of ``FillEvent``s into positions and realized PnL."""

    def __init__(
        self,
        multiplier: float = 1.0,
        *,
        venue: str = "sim",
        multipliers: dict[str, float] | None = None,
        balance_mode: str = "delta",
    ) -> None:
        self.venue = venue
        # _mult holds per-symbol contract multipliers; multiplier_of() falls back to the legacy
        # scalar (the `multiplier` arg) for any symbol not listed, so old single-symbol callers
        # that passed multiplier=K keep getting K (oms.py:54 back-compat).
        self._mult: dict[str, float] = dict(multipliers) if multipliers else {}
        self._mult_default = multiplier
        self.balance_mode = balance_mode
        self.positions: dict[tuple[str, str, str], dict] = {}
        self.realized_pnl: float = 0.0
        self.trades: list[float] = []   # gross price PnL per closing portion, in order
        self.balance: float = 0.0
        self.marks: dict[tuple[str, str], float] = {}
        self.funding_paid: float = 0.0
        self.fees_paid: float = 0.0   # cumulative signed commission (>0 net cost, <0 net rebate)

    def multiplier_of(self, symbol: str) -> float:
        """Per-symbol contract multiplier; the legacy scalar default for unlisted symbols.

        Guarantees multiplier_of('X') == the old self.multiplier for every existing single-symbol
        caller (the value passed as the `multiplier` ctor arg, 1.0 unless a back-compat caller set it).
        """
        return self._mult.get(symbol, self._mult_default)

    def apply_fill(self, fill: "FillEvent") -> None:
        assert fill.venue == self.venue, (
            f"fill.venue={fill.venue!r} routed to Account(venue={self.venue!r})"
        )
        key = (fill.venue, fill.symbol, fill.position_side)
        self._fold(key, fill.side, fill.last_qty, fill.last_px, self.multiplier_of(fill.symbol))
        # Net the trade commission into cash, like apply_liquidation nets its fee. Signed:
        # commission > 0 is a charge (lowers balance), < 0 is a maker rebate (raises it).
        self.balance -= fill.commission
        self.fees_paid += fill.commission

    def _fold(self, key: tuple[str, str, str], side_sign: int, qty: float, px: float,
              mult: float) -> None:
        """Sole writer of position + realized PnL. apply_fill and apply_liquidation both call ONLY
        this for position/realized mutation, so the live ledger and the backtest mirror can never
        drift (one compute_fill block, not two).
        """
        pos = self.positions.get(key)
        prior_size = pos["size"] if pos is not None else 0.0
        prior_avg = pos["avg_px"] if pos is not None else 0.0
        out = compute_fill(prior_size, prior_avg, side_sign, qty, px, mult)
        self.positions[key] = {"size": out.new_size, "avg_px": out.new_avg_px}  # rebind, not in-place mutate
        if out.closing_qty > 0.0:                 # a reduce / close / flip realized PnL on the closed portion
            self.realized_pnl += out.realized_pnl
            self.trades.append(out.realized_pnl)

    def set_mark(self, venue: str, symbol: str, px: float) -> None:
        """Record the latest mark price for unrealized-PnL valuation (perp mark feed, slice 5+)."""
        self.marks[(venue, symbol)] = px

    def unrealized_pnl(self, venue: str, symbol: str, position_side: str = "BOTH") -> float:
        """Mark-to-market PnL on the open position. 0.0 if flat or no mark recorded yet.

        Same shape as compute_fill's realized line (core/fill.py:45) evaluated at the mark:
        (mark - avg_px) * size * multiplier (sign rides in the signed size).
        """
        pos = self.positions.get((venue, symbol, position_side))
        mark = self.marks.get((venue, symbol))
        if pos is None or mark is None:
            return 0.0
        return (mark - pos["avg_px"]) * pos["size"] * self.multiplier_of(symbol)

    def equity(self, initial_cash: float, venue: str = "sim", symbol: str = "X",
               position_side: str = "BOTH") -> float:
        """Compute total equity: initial_cash + balance + realized_pnl + unrealized_pnl.

        Composition mirrors the C2 equity-parity identity exactly:
          initial_cash
          + self.balance          (cumulative -commission + funding cashflows)
          + self.realized_pnl     (gross price PnL of all closed portions)
          + self.unrealized_pnl(venue, symbol, position_side)
                                  (mark-to-market on the open position)

        UNCHANGED SIGNATURE — kept byte-green for C2 (test_sim_equity_parity.py).
        """
        return (
            initial_cash
            + self.balance
            + self.realized_pnl
            + self.unrealized_pnl(venue, symbol, position_side)
        )

    def equity_all(self, seed: float = 0.0) -> float:
        """Mode-aware total equity across ALL open positions (sparse full recompute, no cache).

        delta:         seed + balance + realized_pnl + Σ_open unrealized_pnl(v, s, side)
        authoritative: balance + Σ_open unrealized_pnl(v, s, side)
        (authoritative drops the seed + realized terms — the venue balance is now absolute, so
        realized PnL is already folded into the venue-reported balance.)
        """
        unreal = sum(
            self.unrealized_pnl(v, s, side) for (v, s, side) in self.positions
        )
        if self.balance_mode == "authoritative":
            return self.balance + unreal
        return seed + self.balance + self.realized_pnl + unreal

    def apply_funding(self, ev: "FundingEvent") -> None:
        """Fold a periodic funding cashflow into the cash balance (signed: + received / - paid)."""
        self.balance += ev.amount
        self.funding_paid += ev.amount

    def apply_account_state(self, ev: "AccountState", quote_asset: str = "USDT") -> None:
        """Set balance AUTHORITATIVELY from a venue AccountState event.

        Quote-asset selection strategy (in order):
        1. The ``(asset, qty)`` pair whose asset == ``quote_asset`` (case-sensitive, e.g. 'USDT').
        2. If no match but exactly one balance is present, use that unconditionally (single-asset
           wallet, e.g. BTC-margined account).
        3. If no match and >1 balances, sum all qty values (last resort; covers mixed wallets
           that report multiple stable-coins when no individual asset can be identified).

        This sets ``self.balance`` absolutely (not +=). It is the dead-AccountState consumer
        wired here so downstream WS balance-frame parsing can call apply_account_state and the
        balance stays current without re-seeding from a REST snapshot.

        After each absolute balance assignment, ``self.balance_mode`` is flipped to 'authoritative'
        so that ``equity_all()`` uses the live-balance formula (balance + unrealized only, no seed
        or realized double-count).
        """
        balances = ev.balances
        if not balances:
            return
        for asset, qty in balances:
            if asset == quote_asset:
                self.balance = qty
                self.balance_mode = "authoritative"
                return
        if len(balances) == 1:
            self.balance = balances[0][1]
            self.balance_mode = "authoritative"
            return
        self.balance = sum(qty for _asset, qty in balances)
        self.balance_mode = "authoritative"

    def apply_liquidation(self, ev: "PositionLiquidated") -> None:
        """Forced close: realize PnL at the liq price, close min(ev.qty, held), deduct the liq fee.

        Closes only the venue-reported quantity (``ev.qty``); the residual keeps its cost basis. When
        ``ev.qty`` is falsy (legacy whole-flatten callers / one-way full closes that omit qty) the WHOLE
        held size is closed — byte-equivalent to the pre-5g-1 behavior. ``ev.qty`` is clamped to the held
        size so an over-reported frame can never flip the position. Idempotent on a flat position (a
        same-object replay of a FULL close no-ops via the early return); replay safety for PARTIALs lives
        in LiveOmsHub._seen_liq_ids, mirroring FillEvent's _seen_trade_ids (the Account has no dedup layer).

        Routes through ``_fold`` — the sole position/realized writer — so liquidation and fill PnL math
        use the same compute_fill path (no drift).
        """
        key = (ev.venue, ev.symbol, ev.position_side)
        pos = self.positions.get(key)
        if pos is None or pos["size"] == 0.0:
            return   # true no-op: nothing to liquidate (idempotent on a replayed FULL close —
            # the fee was already deducted on the real close; deducting it here would double-charge)
        close_side = -1 if pos["size"] > 0.0 else 1      # close on the opposite side
        close_qty = min(abs(ev.qty), abs(pos["size"])) if ev.qty else abs(pos["size"])
        self._fold(key, close_side, close_qty, ev.liq_price, self.multiplier_of(ev.symbol))
        self.balance -= ev.fee
