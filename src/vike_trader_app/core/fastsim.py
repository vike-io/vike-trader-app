"""Compiled signal-array backtest kernel — the fast path mirroring engine.py semantics.

The Python ``BacktestEngine`` stays the source of truth; this runs equivalent
*signal-array* strategies in an ``@njit`` inner loop (pure-numpy fallback when the
optional ``[fast]`` extra is absent), matching the engine's numbers within float
tolerance: next-open fills, long/short, maker/taker fees, slippage, perp funding,
cost-basis averaging.
"""

import numpy as np

from .model import Trade

try:  # optional accelerator (vike_trader_app[fast]); falls back to numpy when absent
    from numba import njit

    _HAS_NUMBA = True
except ImportError:  # pragma: no cover - exercised only without the extra
    _HAS_NUMBA = False

    def njit(*args, **kwargs):
        def _decorator(fn):
            return fn

        return _decorator if (args and callable(args[0])) is False else args[0]


@njit(cache=True)
def _sim_kernel(opens, highs, lows, closes, funding, ts,
                entries, exits, size, side,
                taker_fee, slippage, init_cash):  # pragma: no cover - compiled
    """One-pass simulation. Returns (equity_curve, n_trades, trade-component arrays).

    Decision rule at bar i (after this bar's pending fills + funding) mirrors
    ``_ArrayStrategy.on_bar``: exit -> close to flat; entry -> open when flat or
    immediately after an exit (a flip = two orders, close then open). Orders
    submitted at bar i fill at ``open[i+1]`` (next-open). No pyramiding.
    """
    # highs/lows are reserved for future intrabar limit/stop fills; unused in v1.
    n = closes.shape[0]
    equity = np.empty(n, dtype=np.float64)
    tr_entry_p = np.empty(n, dtype=np.float64)
    tr_exit_p = np.empty(n, dtype=np.float64)
    tr_size = np.empty(n, dtype=np.float64)
    tr_pnl = np.empty(n, dtype=np.float64)
    tr_fees = np.empty(n, dtype=np.float64)
    tr_entry_ts = np.empty(n, dtype=np.int64)
    tr_exit_ts = np.empty(n, dtype=np.int64)
    nt = 0

    cash = init_cash
    pos = 0.0
    avg = 0.0
    entry_fee = 0.0
    entry_ts = 0

    # pending order slots for next-bar fill (max 2: close then open)
    p_cnt = 0
    p_side0 = 0
    p_size0 = 0.0
    p_side1 = 0
    p_size1 = 0.0

    for i in range(n):
        # 1) fill pending orders at this bar's open (next-open semantics)
        for k in range(p_cnt):
            o_side = p_side0 if k == 0 else p_side1
            o_size = p_size0 if k == 0 else p_size1
            price = opens[i] * (1.0 + o_side * slippage)   # adverse: buys up, sells down
            fee = o_size * price * taker_fee
            cash -= fee
            delta = o_side * o_size
            if pos == 0.0:                                  # open
                pos = delta
                avg = price
                cash -= delta * price
                entry_fee = fee
                entry_ts = ts[i]
            elif (pos > 0.0) == (delta > 0.0):              # add same direction
                new = pos + delta
                avg = (avg * abs(pos) + price * abs(delta)) / abs(new)
                pos = new
                cash -= delta * price
                entry_fee += fee
            else:                                           # close (full)
                closed = pos
                cash -= delta * price
                tr_entry_p[nt] = avg
                tr_exit_p[nt] = price
                tr_size[nt] = abs(closed)
                tr_pnl[nt] = (price - avg) * closed
                tr_fees[nt] = entry_fee + fee
                tr_entry_ts[nt] = entry_ts
                tr_exit_ts[nt] = ts[i]
                nt += 1
                pos = 0.0
                avg = 0.0
                entry_fee = 0.0
                entry_ts = 0
        p_cnt = 0

        # 2) perp funding on the held position (funding[i] == 0.0 when absent)
        if pos != 0.0 and funding[i] != 0.0:
            cash -= pos * closes[i] * funding[i]

        # 3) mark-to-market equity
        equity[i] = cash + pos * closes[i]

        # 4) decide next-bar orders from signals (orders on the last bar never fill)
        do_exit = exits[i] and pos != 0.0
        do_entry = entries[i] and (pos == 0.0 or do_exit)
        if do_exit:
            p_side0 = -1 if pos > 0.0 else 1
            p_size0 = abs(pos)
            if do_entry:
                p_side1 = 1 if side[i] > 0 else -1
                p_size1 = size[i]
                p_cnt = 2
            else:
                p_cnt = 1
        elif do_entry:
            p_side0 = 1 if side[i] > 0 else -1
            p_size0 = size[i]
            p_cnt = 1

    return (equity, nt, tr_entry_p, tr_exit_p, tr_size, tr_pnl, tr_fees,
            tr_entry_ts, tr_exit_ts)


def fast_backtest(opens, highs, lows, closes, funding, ts,
                  entries, exits, size, side,
                  *, maker_fee=0.0, taker_fee=0.0, slippage=0.0, init_cash=10_000.0):
    """Run a signal-array backtest through the compiled kernel.

    Signals are market-style (taker). ``maker_fee`` is accepted for API symmetry but
    unused in v1 (no resting orders). ``highs``/``lows`` are accepted (full OHLC) but
    unused in v1 — reserved for intrabar limit/stop fills in a later phase.

    Returns a dict with keys ``trades`` (list[Trade]), ``equity_curve`` (list[float]),
    ``final_equity`` (float), and ``n_trades`` (int).
    """
    opens = np.asarray(opens, np.float64)
    highs = np.asarray(highs, np.float64)
    lows = np.asarray(lows, np.float64)
    closes = np.asarray(closes, np.float64)
    funding = np.asarray(funding, np.float64)
    ts = np.asarray(ts, np.int64)
    entries = np.asarray(entries, np.bool_)
    exits = np.asarray(exits, np.bool_)
    size = np.asarray(size, np.float64)
    side = np.asarray(side, np.int64)

    (equity, nt, e_p, x_p, sz, pnl, fees, e_ts, x_ts) = _sim_kernel(
        opens, highs, lows, closes, funding, ts, entries, exits, size, side,
        float(taker_fee), float(slippage), float(init_cash),
    )
    trades = [
        Trade(entry_price=float(e_p[k]), exit_price=float(x_p[k]), size=float(sz[k]),
              pnl=float(pnl[k]), fees=float(fees[k]),
              entry_ts=int(e_ts[k]), exit_ts=int(x_ts[k]))
        for k in range(nt)
    ]
    return {
        "trades": trades,
        "equity_curve": equity.tolist(),
        "final_equity": float(equity[-1]) if len(equity) else init_cash,
        "n_trades": int(nt),
    }
