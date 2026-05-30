"""Compiled signal-array backtest kernel — the fast path mirroring engine.py semantics.

The Python ``BacktestEngine`` stays the source of truth; this runs equivalent
*signal-array* strategies in an ``@njit`` inner loop (pure-numpy fallback when the
optional ``[fast]`` extra is absent), matching the engine's numbers within float
tolerance: next-open fills, long/short, maker/taker fees, slippage, perp funding,
cost-basis averaging.
"""

import numpy as np

from .model import Trade

def _noop_njit(*args, **kwargs):
    """Fallback for ``numba.njit`` when the optional ``[fast]`` extra is absent.

    Returns the wrapped function unchanged. Supports both the bare ``@_noop_njit``
    form and the parametrized ``@_noop_njit(cache=True)`` form.
    """
    if args and callable(args[0]):        # bare @_noop_njit applied directly to a function
        return args[0]

    def _decorator(fn):                    # parametrized @_noop_njit(...) -> returns a decorator
        return fn

    return _decorator


try:  # optional accelerator (vike_trader_app[fast]); falls back to a no-op when absent
    from numba import njit

    _HAS_NUMBA = True
except ImportError:  # pragma: no cover - exercised only without the extra
    _HAS_NUMBA = False
    njit = _noop_njit


@njit(cache=True)
def _sim_kernel(opens, highs, lows, closes, funding, cashflow, ts,
                entries, exits, size, side,
                taker_fee, slippage, init_cash,
                multiplier, leverage, maint_margin, size_type):  # pragma: no cover - compiled
    """One-pass simulation mirroring engine.py. Returns (equity_curve, n_trades, trade arrays).

    Per-bar order: fill pending (next-open) -> funding -> cashflow -> liquidation ->
    equity mark -> decide next-bar orders. ``multiplier`` scales every notional term.
    ``leverage<=0`` means unlimited; ``maint_margin<=0`` disables liquidation;
    ``size_type`` 0=shares/1=value/2=percent reinterprets ``size`` at decision time.
    """
    n = closes.shape[0]
    equity = np.empty(n, dtype=np.float64)
    cap = 2 * n  # a bar can close a pending position AND liquidate -> up to 2 trades/bar
    tr_entry_p = np.empty(cap, dtype=np.float64)
    tr_exit_p = np.empty(cap, dtype=np.float64)
    tr_size = np.empty(cap, dtype=np.float64)
    tr_pnl = np.empty(cap, dtype=np.float64)
    tr_fees = np.empty(cap, dtype=np.float64)
    tr_entry_ts = np.empty(cap, dtype=np.int64)
    tr_exit_ts = np.empty(cap, dtype=np.int64)
    nt = 0

    cash = init_cash
    pos = 0.0
    avg = 0.0
    entry_fee = 0.0
    entry_ts = 0

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
            price = opens[i] * (1.0 + o_side * slippage)
            fee = o_size * price * taker_fee * multiplier
            cash -= fee
            delta = o_side * o_size
            if pos == 0.0:
                pos = delta
                avg = price
                cash -= delta * price * multiplier
                entry_fee = fee
                entry_ts = ts[i]
            elif (pos > 0.0) == (delta > 0.0):
                new = pos + delta
                avg = (avg * abs(pos) + price * abs(delta)) / abs(new)
                pos = new
                cash -= delta * price * multiplier
                entry_fee += fee
            else:
                closed = pos
                cash -= delta * price * multiplier
                tr_entry_p[nt] = avg
                tr_exit_p[nt] = price
                tr_size[nt] = abs(closed)
                tr_pnl[nt] = (price - avg) * closed * multiplier
                tr_fees[nt] = entry_fee + fee
                tr_entry_ts[nt] = entry_ts
                tr_exit_ts[nt] = ts[i]
                nt += 1
                pos = 0.0
                avg = 0.0
                entry_fee = 0.0
                entry_ts = 0
        p_cnt = 0

        # 2) perp funding on the held position
        if pos != 0.0 and funding[i] != 0.0:
            cash -= pos * closes[i] * funding[i] * multiplier

        # 3) cashflow (deposits/withdrawals); zeros by default  [Task 2 inserts here]

        # 4) liquidation check  [Task 5 inserts here]

        # 5) mark-to-market equity
        equity[i] = cash + pos * closes[i] * multiplier

        # 6) entry share count from size[i] honoring size_type + leverage cap
        ent_sh = size[i]  # [Task 3 inserts size_type conversion; Task 4 inserts leverage cap]

        # 7) decide next-bar orders
        do_exit = exits[i] and pos != 0.0
        do_entry = entries[i] and (pos == 0.0 or do_exit)
        if do_exit:
            p_side0 = -1 if pos > 0.0 else 1
            p_size0 = abs(pos)
            if do_entry:
                p_side1 = 1 if side[i] > 0 else -1
                p_size1 = ent_sh
                p_cnt = 2
            else:
                p_cnt = 1
        elif do_entry:
            p_side0 = 1 if side[i] > 0 else -1
            p_size0 = ent_sh
            p_cnt = 1

    return (equity, nt, tr_entry_p, tr_exit_p, tr_size, tr_pnl, tr_fees,
            tr_entry_ts, tr_exit_ts)


def fast_backtest(opens, highs, lows, closes, funding, ts,
                  entries, exits, size, side,
                  *, maker_fee=0.0, taker_fee=0.0, slippage=0.0, init_cash=10_000.0,
                  build_trades=True, multiplier=1.0, leverage=None, maint_margin=0.0,
                  size_type="shares", cashflow=None):
    """Run a signal-array backtest through the compiled kernel.

    Signals are market-style (taker). ``maker_fee`` is accepted for API symmetry but unused.
    ``multiplier`` scales every notional term. ``leverage`` (None=unlimited) caps order notional
    at decision time; ``maint_margin`` (>0) enables intrabar liquidation at the bar's adverse
    extreme. ``size_type`` is "shares" | "value" | "percent". ``cashflow`` is an optional per-bar
    deposit/withdrawal sequence. Pass ``build_trades=False`` to skip ``Trade`` construction.

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
    n = closes.shape[0]
    cashflow = np.zeros(n, np.float64) if cashflow is None else np.asarray(cashflow, np.float64)
    lev = 0.0 if leverage is None else float(leverage)
    st = {"shares": 0, "value": 1, "percent": 2}[size_type]

    (equity, nt, e_p, x_p, sz, pnl, fees, e_ts, x_ts) = _sim_kernel(
        opens, highs, lows, closes, funding, cashflow, ts, entries, exits, size, side,
        float(taker_fee), float(slippage), float(init_cash),
        float(multiplier), lev, float(maint_margin), st,
    )
    if build_trades:
        trades = [
            Trade(entry_price=float(e_p[k]), exit_price=float(x_p[k]), size=float(sz[k]),
                  pnl=float(pnl[k]), fees=float(fees[k]),
                  entry_ts=int(e_ts[k]), exit_ts=int(x_ts[k]))
            for k in range(nt)
        ]
    else:
        trades = []
    return {
        "trades": trades,
        "equity_curve": equity.tolist(),
        "final_equity": float(equity[-1]) if len(equity) else init_cash,
        "n_trades": int(nt),
    }
