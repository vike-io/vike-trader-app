"""Compiled multi-asset (time×symbol) portfolio backtest kernel — the vectorized fast
path for cross-sectional / target-weight strategies.

The Python :class:`~vike_trader_app.core.multi_symbol_engine.MultiSymbolEngine` stays the
source of truth; this runs equivalent *target-weight* strategies in an ``@njit`` inner loop
(pure-Python/numpy fallback when the optional ``[fast]`` extra is absent), matching the
engine's numbers to float tolerance.

SCOPE (the parity boundary — deliberately narrow):
    This kernel reproduces the ``cash_gate=False`` market-order target-weight path ONLY:
    next-open fills, column-order shared-cash application (cash MAY go negative — NO cash
    gate, NO reduce-first ordering, NO weight priority), taker fees, slippage, perp funding,
    cost-basis averaging via :func:`compute_fill_nb`.

    OUT OF SCOPE (use ``MultiSymbolEngine`` directly): protective stops / SL-TP brackets,
    the shared-cash drop gate (``cash_gate=True``), %-of-volume liquidity caps, MaxOpen*
    position caps, leverage notional capping, dynamic membership masks, liquidation,
    granular sub-bar fills, sizers. ``leverage``/``maint_margin`` are accepted for API
    symmetry but are NOT enforced here (a strategy needing them must use the event engine).

The cost math is NOT re-inlined here: every fee / slippage / funding / position-transition
term is delegated to the shared ``@njit`` primitives in :mod:`vike_trader_app.core.fill_njit`
(``adverse_fill_price_nb`` / ``fee_nb`` / ``funding_charge_nb`` / ``compute_fill_nb``), the
same core the single-asset path and the event engine pin against.

THE TARGET-WEIGHTS CONTRACT
    ``target_weights`` is a ``(T, S)`` float64 matrix.  ``target_weights[t, s]`` is the
    weight DECIDED at bar ``t`` (using closes ≤ ``t``), to be realized at bar ``t+1``'s open.
    A weight of ``0.0`` for a held symbol drives a full close.  The last bar's row is never
    acted on (there is no ``t+1`` to fill it).

    NON-REBALANCE BARS: a row that is all-``NaN`` means "no rebalance decided at this bar" —
    the kernel queues NOTHING for that bar, exactly as ``MultiSymbolEngine`` queues nothing on
    a bar where the schedule does not fire.  This is what makes parity exact: the event engine
    only re-trades on rebalance bars, so the caller emits real weights ONLY on rebalance bars
    (``0.0`` for non-held non-winners and for held drop-outs) and ``NaN`` everywhere else.  A
    per-element ``NaN`` is also skipped per symbol, but the natural use is whole-row ``NaN``.
"""

from __future__ import annotations

import numpy as np

from .fill_njit import (
    KIND_ADD,
    KIND_CLOSE,
    KIND_FLIP,
    KIND_OPEN,
    KIND_REDUCE,
    adverse_fill_price_nb,
    compute_fill_nb,
    fee_nb,
    funding_charge_nb,
    njit,
)
from .model import Trade

_DEAD_BAND = 1e-12  # |delta shares| <= this -> skip (mirrors Strategy._engine_target)


@njit(cache=True)
def _portfolio_kernel(opens, closes, funding, ts, target_weights,
                      taker_fee, slippage, init_cash, multiplier):  # pragma: no cover - compiled
    """One-pass time×symbol simulation mirroring ``MultiSymbolEngine.run`` (cash_gate=False).

    Inputs are ``(T, S)`` float64 matrices (``ts`` is ``(T,)`` int64). ``target_weights[t, s]``
    is the weight decided at bar ``t`` to be realized at ``t+1``'s open.

    Per bar ``t``:
      1. Fill the deltas QUEUED at bar ``t-1`` at ``opens[t, s]`` — per symbol in COLUMN order,
         on shared ``cash`` (cash may go negative; no gate). Records Trades on reduce/close/flip.
      2. Apply perp funding on held positions (marked at ``closes[t, s]``).
      3. Mark equity = ``cash + Σ pos[s]·closes[t, s]·mult``.
      4. Compute next deltas from ``target_weights[t]`` against the SAME equity snapshot:
         ``target_shares = w·equity / closes[t, s] − pos[s]``; dead-band ``1e-12``; queue for ``t+1``.

    Returns ``(equity, n_trades, tr_*)`` flat trade arrays (mirrors the single-asset kernel).
    """
    T = closes.shape[0]
    S = closes.shape[1]
    equity = np.empty(T, dtype=np.float64)

    pos = np.zeros(S, dtype=np.float64)
    avg = np.zeros(S, dtype=np.float64)
    entry_fee = np.zeros(S, dtype=np.float64)
    entry_ts = np.zeros(S, dtype=np.int64)

    # Pending deltas queued at bar t-1, filled at bar t's open. queued_qty>0 means an order
    # of `queued_side * queued_qty` shares is pending for that symbol. At most one queued
    # market order per symbol per bar (a rebalance emits one net delta per symbol).
    queued_side = np.zeros(S, dtype=np.int64)
    queued_qty = np.zeros(S, dtype=np.float64)

    # Trade record arrays. A rebalance can at most flip every symbol once per bar -> 1 trade
    # per symbol per bar bounds the count at T*S.
    cap = T * S + 1
    tr_entry_p = np.empty(cap, dtype=np.float64)
    tr_exit_p = np.empty(cap, dtype=np.float64)
    tr_size = np.empty(cap, dtype=np.float64)
    tr_pnl = np.empty(cap, dtype=np.float64)
    tr_fees = np.empty(cap, dtype=np.float64)
    tr_entry_ts = np.empty(cap, dtype=np.int64)
    tr_exit_ts = np.empty(cap, dtype=np.int64)
    tr_sym = np.empty(cap, dtype=np.int64)
    nt = 0

    cash = init_cash

    for t in range(T):
        # 1) Fill deltas queued at t-1 at THIS bar's open, in column order, on shared cash.
        for s in range(S):
            if queued_qty[s] <= 0.0:
                continue
            side = queued_side[s]
            qty = queued_qty[s]
            raw_open = opens[t, s]
            fill_px = adverse_fill_price_nb(raw_open, side, slippage)
            fee = fee_nb(qty, fill_px, taker_fee, multiplier)
            delta_signed = side * qty
            cash -= fee                                       # transaction cost
            cash -= delta_signed * fill_px * multiplier       # signed notional moves cash

            (kind, new_size, new_avg, closing_qty, entry_avg_px,
             realized, portion, leftover) = compute_fill_nb(
                pos[s], avg[s], side, qty, fill_px, multiplier)

            if kind == KIND_OPEN:
                pos[s] = new_size
                avg[s] = new_avg
                entry_fee[s] = fee
                entry_ts[s] = ts[t]
            elif kind == KIND_ADD:
                pos[s] = new_size
                avg[s] = new_avg
                entry_fee[s] += fee
            else:
                # reduce / close / flip -> a closed-portion Trade
                entry_fee_portion = entry_fee[s] * portion
                exit_fee_portion = fee * (closing_qty / qty)  # qty>0 here (queued_qty>0)
                tr_entry_p[nt] = entry_avg_px
                tr_exit_p[nt] = fill_px
                tr_size[nt] = closing_qty
                tr_pnl[nt] = realized
                tr_fees[nt] = entry_fee_portion + exit_fee_portion
                tr_entry_ts[nt] = entry_ts[s]
                tr_exit_ts[nt] = ts[t]
                tr_sym[nt] = s
                nt += 1
                pos[s] = new_size
                avg[s] = new_avg
                if kind == KIND_REDUCE:
                    entry_fee[s] -= entry_fee_portion
                elif kind == KIND_FLIP:
                    entry_fee[s] = fee * (leftover / qty)
                    entry_ts[s] = ts[t]
                else:  # KIND_CLOSE -> flat
                    entry_fee[s] = 0.0
                    entry_ts[s] = 0
            # consume the queued order
            queued_qty[s] = 0.0
            queued_side[s] = 0

        # 2) Perp funding on held positions (matches run(): marked at close).
        for s in range(S):
            f = funding[t, s]
            if f != 0.0 and pos[s] != 0.0:
                cash -= funding_charge_nb(pos[s], closes[t, s], f, multiplier)

        # 3) Mark-to-market equity (single shared snapshot — used both for the curve and sizing).
        eq = cash
        for s in range(S):
            eq += pos[s] * closes[t, s] * multiplier
        equity[t] = eq

        # 4) Decide next-bar deltas from this bar's target weights, against the eq snapshot.
        #    (No t+1 to fill the last bar's decision, so skip it — matches the engine: the
        #     rebalance at bar T-1 queues orders that never fill.)
        if t + 1 < T:
            for s in range(S):
                w = target_weights[t, s]
                if w != w:                         # NaN -> no rebalance decided for this symbol/bar
                    continue
                c = closes[t, s]
                if c == 0.0:
                    continue                       # mirror _engine_target's price<=0 guard
                target_shares = w * eq / c
                delta = target_shares - pos[s]
                if delta > _DEAD_BAND:
                    queued_side[s] = 1
                    queued_qty[s] = delta
                elif delta < -_DEAD_BAND:
                    queued_side[s] = -1
                    queued_qty[s] = -delta
                # else: |delta| <= dead-band -> no order

    return (equity, nt, tr_entry_p, tr_exit_p, tr_size, tr_pnl, tr_fees,
            tr_entry_ts, tr_exit_ts, tr_sym)


def fast_portfolio_backtest(opens, highs, lows, closes, funding, ts, target_weights,
                            *, taker_fee=0.0, slippage=0.0, init_cash=10_000.0,
                            multiplier=1.0, leverage=None, maint_margin=0.0,
                            symbols=None, build_trades=True):
    """Run a target-weight portfolio backtest through the compiled time×symbol kernel.

    All price inputs are ``(T, S)`` float64 matrices; ``ts`` is a ``(T,)`` int64 vector
    (shared aligned timeline). ``target_weights`` is a ``(T, S)`` matrix: ``[t, s]`` is the
    weight decided at bar ``t`` to be realized at ``t+1``'s open (carry-forward between
    rebalances is the caller's job).

    ``leverage`` / ``maint_margin`` are accepted for API symmetry but NOT enforced (see the
    module scope note) — pass a strategy needing them through the event engine instead.
    ``symbols`` (optional list[str]) labels per-symbol trades. Returns a dict duck-compatible
    with ``MultiSymbolResult`` where the parity test reads it: keys ``trades`` (list[Trade]),
    ``equity_curve`` (list[float]), ``final_equity`` (float), ``n_trades`` (int).
    """
    opens = np.ascontiguousarray(opens, np.float64)
    highs = np.ascontiguousarray(highs, np.float64)
    lows = np.ascontiguousarray(lows, np.float64)
    closes = np.ascontiguousarray(closes, np.float64)
    funding = np.ascontiguousarray(funding, np.float64)
    ts = np.ascontiguousarray(ts, np.int64)
    target_weights = np.ascontiguousarray(target_weights, np.float64)

    if closes.ndim != 2:
        raise ValueError("closes must be a (T, S) matrix")
    T, S = closes.shape
    for name, arr in (("opens", opens), ("highs", highs), ("lows", lows),
                      ("funding", funding), ("target_weights", target_weights)):
        if arr.shape != (T, S):
            raise ValueError(f"{name} must have shape {(T, S)}, got {arr.shape}")
    if ts.shape != (T,):
        raise ValueError(f"ts must have shape {(T,)}, got {ts.shape}")

    # highs/lows are accepted + validated for API symmetry with the single-asset kernel and
    # reserved for future MAE/MFE tracking, but the cross-sectional sim itself fills at the
    # open and marks at the close, so they are not passed into the inner kernel.
    (equity, nt, e_p, x_p, sz, pnl, fees, e_ts, x_ts, sym) = _portfolio_kernel(
        opens, closes, funding, ts, target_weights,
        float(taker_fee), float(slippage), float(init_cash), float(multiplier),
    )

    if build_trades:
        trades = [
            Trade(
                entry_price=float(e_p[k]), exit_price=float(x_p[k]), size=float(sz[k]),
                pnl=float(pnl[k]), fees=float(fees[k]),
                entry_ts=int(e_ts[k]), exit_ts=int(x_ts[k]),
                symbol=(symbols[int(sym[k])] if symbols is not None else ""),
            )
            for k in range(nt)
        ]
    else:
        trades = []

    return {
        "trades": trades,
        "equity_curve": equity.tolist(),
        "final_equity": float(equity[-1]) if T else init_cash,
        "n_trades": int(nt),
    }


class CrossSectionalSignalStrategy:
    """Vectorized cross-sectional analog of ``SignalStrategy.signals``.

    Subclass and override :meth:`target_weights` to return a ``(T, S)`` weight matrix for the
    universe (the cross-sectional counterpart to the single-asset ``signals(close) -> (entries,
    exits)``).  ``target_weights[t, s]`` is the weight DECIDED at bar ``t`` to be realized at
    ``t+1``'s open; a row of ``NaN`` means "no rebalance this bar" (queue nothing).  Then
    :meth:`run` feeds it through :func:`fast_portfolio_backtest`.

    ``data`` is a dict with ``(T, S)`` arrays under ``open``/``high``/``low``/``close`` and an
    optional ``(T, S)`` ``funding`` (defaults to zeros) + ``(T,)`` ``ts`` (defaults to a bar
    index) + ``symbols`` (list[str], column labels).  This mirrors the ``MultiSymbolEngine``
    ``bars_by_symbol`` input, transposed into the column-aligned matrix form the kernel wants.
    """

    def target_weights(self, data: dict) -> np.ndarray:
        """Return the ``(T, S)`` target-weight matrix for ``data`` (override). See class doc."""
        raise NotImplementedError

    def run(self, data: dict, *, taker_fee=0.0, slippage=0.0, init_cash=10_000.0,
            multiplier=1.0, leverage=None, maint_margin=0.0, build_trades=True) -> dict:
        """Build the weight matrix via :meth:`target_weights` and run the kernel."""
        closes = np.ascontiguousarray(data["close"], np.float64)
        T, S = closes.shape
        opens = np.ascontiguousarray(data["open"], np.float64)
        highs = np.ascontiguousarray(data["high"], np.float64)
        lows = np.ascontiguousarray(data["low"], np.float64)
        funding = (np.ascontiguousarray(data["funding"], np.float64)
                   if data.get("funding") is not None else np.zeros((T, S), np.float64))
        ts = (np.ascontiguousarray(data["ts"], np.int64)
              if data.get("ts") is not None else np.arange(T, dtype=np.int64))
        symbols = data.get("symbols")
        weights = self.target_weights(data)
        return fast_portfolio_backtest(
            opens, highs, lows, closes, funding, ts, weights,
            taker_fee=taker_fee, slippage=slippage, init_cash=init_cash,
            multiplier=multiplier, leverage=leverage, maint_margin=maint_margin,
            symbols=symbols, build_trades=build_trades,
        )
