"""Parity tests: fill_njit vs the Python source-of-truth (fill.py + broker_sim.py).

All inputs are deterministic grids — no randomness, fully reproducible.
"""

from __future__ import annotations

import math
import pytest

from vike_trader_app.core import fill as _fill
from vike_trader_app.core import broker_sim as _bsim
from vike_trader_app.core import fill_njit as _fn
from vike_trader_app.core.fill_njit import (
    KIND_OPEN, KIND_ADD, KIND_REDUCE, KIND_CLOSE, KIND_FLIP,
    FILL_KIND_NAMES, FILL_KIND_INTS,
    adverse_fill_price_nb, fee_nb, funding_charge_nb, compute_fill_nb,
)

_TOL = 1e-9


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _unpack(t: tuple) -> dict:
    """Name the 8-tuple fields for readable assertions."""
    kind_int, new_size, new_avg_px, closing_qty, entry_avg_px, realized_pnl, portion, leftover = t
    return dict(
        kind_int=kind_int,
        new_size=new_size,
        new_avg_px=new_avg_px,
        closing_qty=closing_qty,
        entry_avg_px=entry_avg_px,
        realized_pnl=realized_pnl,
        portion=portion,
        leftover=leftover,
    )


def _assert_match(nb_tuple: tuple, py_outcome: "_fill.FillOutcome", label: str = "") -> None:
    nb = _unpack(nb_tuple)
    # kind
    assert nb["kind_int"] == FILL_KIND_INTS[py_outcome.kind], (
        f"{label}: kind mismatch nb={FILL_KIND_NAMES[nb['kind_int']]} py={py_outcome.kind}"
    )
    for field in ("new_size", "new_avg_px", "closing_qty", "entry_avg_px",
                  "realized_pnl", "portion", "leftover"):
        py_val = getattr(py_outcome, field)
        nb_val = nb[field]
        assert abs(nb_val - py_val) < _TOL, (
            f"{label}: {field} mismatch nb={nb_val} py={py_val}"
        )


# --------------------------------------------------------------------------- #
# Scalar helpers parity                                                        #
# --------------------------------------------------------------------------- #

class TestScalarHelpers:
    """Grid-test the 3 scalar helpers against broker_sim."""

    # representative value grids
    _PRICES = [0.01, 1.0, 100.0, 50_000.0]
    _SLIPPAGES = [0.0, 0.0005, 0.001, 0.01]
    _RATES = [0.0, 0.0001, 0.0006, 0.005]
    _MULTS = [1.0, 10.0, 0.001]
    _SIZES = [0.1, 1.0, 10.0, 1000.0]
    _POSES = [-500.0, -1.0, 0.0, 1.0, 500.0]
    _MARK_PRICES = [0.5, 100.0, 50_000.0]
    _FUNDING_RATES = [-0.001, 0.0, 0.0003, 0.01]

    def test_adverse_fill_price(self):
        for raw in self._PRICES:
            for slip in self._SLIPPAGES:
                for side in (+1, -1):
                    nb = adverse_fill_price_nb(raw, side, slip)
                    py = _bsim.adverse_fill_price(raw, side, slip)
                    assert abs(nb - py) < _TOL, f"raw={raw} side={side} slip={slip}"

    def test_fee(self):
        for sz in self._SIZES:
            for price in self._PRICES:
                for rate in self._RATES:
                    for mult in self._MULTS:
                        nb = fee_nb(sz, price, rate, mult)
                        py = _bsim.fee(sz, price, rate, mult)
                        assert abs(nb - py) < _TOL, f"sz={sz} p={price} r={rate} m={mult}"

    def test_funding_charge(self):
        for pos in self._POSES:
            for mark in self._MARK_PRICES:
                for rate in self._FUNDING_RATES:
                    for mult in self._MULTS:
                        nb = funding_charge_nb(pos, mark, rate, mult)
                        py = _bsim.funding_charge(pos, mark, rate, mult)
                        assert abs(nb - py) < _TOL, (
                            f"pos={pos} mark={mark} rate={rate} mult={mult}"
                        )


# --------------------------------------------------------------------------- #
# compute_fill_nb parity — branch by branch                                   #
# --------------------------------------------------------------------------- #

class TestComputeFillNbParity:
    """Exhaustive branch coverage for compute_fill_nb vs fill.compute_fill.

    Branches:
        B0 — open from flat (prior_size == 0)
        B1 — add same direction (long adds to long, short adds to short)
        B2 — partial reduce (|delta| < |prior_size|)
        B3 — full close (|delta| == |prior_size|  → flat)
        B4 — flip through zero (|delta| > |prior_size|)
    All branches tested for both long and short prior positions (and long/short sides).
    multiplier values: 1.0 and 10.0.
    """

    _PRICES = [1.0, 100.0, 50_000.0]
    _QTYS = [1.0, 5.0, 100.0]
    _MULTS = [1.0, 10.0]

    # ---- B0: open from flat ----------------------------------------------

    def test_open_long(self):
        for price in self._PRICES:
            for qty in self._QTYS:
                for mult in self._MULTS:
                    nb = compute_fill_nb(0.0, 0.0, +1, qty, price, mult)
                    py = _fill.compute_fill(0.0, 0.0, +1, qty, price, mult)
                    _assert_match(nb, py, f"open_long p={price} q={qty} m={mult}")
                    assert nb[0] == KIND_OPEN

    def test_open_short(self):
        for price in self._PRICES:
            for qty in self._QTYS:
                for mult in self._MULTS:
                    nb = compute_fill_nb(0.0, 0.0, -1, qty, price, mult)
                    py = _fill.compute_fill(0.0, 0.0, -1, qty, price, mult)
                    _assert_match(nb, py, f"open_short p={price} q={qty} m={mult}")
                    assert nb[0] == KIND_OPEN

    # ---- B1: add same direction ------------------------------------------

    def test_add_to_long(self):
        """Buy more when already long."""
        prior_sizes = [1.0, 5.0, 100.0]
        prior_avgs  = [50.0, 100.0, 1000.0]
        for ps in prior_sizes:
            for pa in prior_avgs:
                for price in self._PRICES:
                    for qty in self._QTYS:
                        for mult in self._MULTS:
                            nb = compute_fill_nb(ps, pa, +1, qty, price, mult)
                            py = _fill.compute_fill(ps, pa, +1, qty, price, mult)
                            _assert_match(nb, py, f"add_long ps={ps} pa={pa} p={price} q={qty}")
                            assert nb[0] == KIND_ADD

    def test_add_to_short(self):
        """Sell more when already short."""
        prior_sizes = [-1.0, -5.0, -100.0]
        prior_avgs  = [50.0, 100.0, 1000.0]
        for ps in prior_sizes:
            for pa in prior_avgs:
                for price in self._PRICES:
                    for qty in self._QTYS:
                        for mult in self._MULTS:
                            nb = compute_fill_nb(ps, pa, -1, qty, price, mult)
                            py = _fill.compute_fill(ps, pa, -1, qty, price, mult)
                            _assert_match(nb, py, f"add_short ps={ps} pa={pa} p={price} q={qty}")
                            assert nb[0] == KIND_ADD

    # ---- B2: partial reduce (reduce) ------------------------------------

    def test_partial_reduce_long(self):
        """Sell some of a long position — remainder survives at same avg."""
        # prior 10 units long, sell 3 (< 10 → reduce)
        cases = [
            (10.0, 100.0, -1, 3.0),
            (5.0,  200.0, -1, 2.0),
            (100.0, 50_000.0, -1, 40.0),
        ]
        for ps, pa, side, qty in cases:
            for price in self._PRICES:
                for mult in self._MULTS:
                    nb = compute_fill_nb(ps, pa, side, qty, price, mult)
                    py = _fill.compute_fill(ps, pa, side, qty, price, mult)
                    _assert_match(nb, py, f"reduce_long ps={ps} pa={pa} q={qty} p={price}")
                    assert nb[0] == KIND_REDUCE

    def test_partial_reduce_short(self):
        """Buy back some of a short position."""
        cases = [
            (-10.0, 100.0, +1, 3.0),
            (-5.0,  200.0, +1, 2.0),
            (-100.0, 50_000.0, +1, 40.0),
        ]
        for ps, pa, side, qty in cases:
            for price in self._PRICES:
                for mult in self._MULTS:
                    nb = compute_fill_nb(ps, pa, side, qty, price, mult)
                    py = _fill.compute_fill(ps, pa, side, qty, price, mult)
                    _assert_match(nb, py, f"reduce_short ps={ps} pa={pa} q={qty} p={price}")
                    assert nb[0] == KIND_REDUCE

    # ---- B3: full close --------------------------------------------------

    def test_full_close_long(self):
        """Sell exactly the long position — goes flat."""
        cases = [
            (10.0, 100.0, -1, 10.0),
            (5.0,  200.0, -1, 5.0),
            (1.0,  50_000.0, -1, 1.0),
        ]
        for ps, pa, side, qty in cases:
            for price in self._PRICES:
                for mult in self._MULTS:
                    nb = compute_fill_nb(ps, pa, side, qty, price, mult)
                    py = _fill.compute_fill(ps, pa, side, qty, price, mult)
                    _assert_match(nb, py, f"close_long ps={ps} pa={pa} q={qty} p={price}")
                    assert nb[0] == KIND_CLOSE

    def test_full_close_short(self):
        """Cover exactly the short position — goes flat."""
        cases = [
            (-10.0, 100.0, +1, 10.0),
            (-5.0,  200.0, +1, 5.0),
            (-1.0,  50_000.0, +1, 1.0),
        ]
        for ps, pa, side, qty in cases:
            for price in self._PRICES:
                for mult in self._MULTS:
                    nb = compute_fill_nb(ps, pa, side, qty, price, mult)
                    py = _fill.compute_fill(ps, pa, side, qty, price, mult)
                    _assert_match(nb, py, f"close_short ps={ps} pa={pa} q={qty} p={price}")
                    assert nb[0] == KIND_CLOSE

    # ---- B4: flip through zero -------------------------------------------

    def test_flip_long_to_short(self):
        """Sell more than held long — new short position opened at fill price."""
        cases = [
            (5.0,  100.0, -1, 8.0),    # close 5, open 3 short
            (1.0,  200.0, -1, 10.0),   # close 1, open 9 short
            (10.0, 50_000.0, -1, 15.0),
        ]
        for ps, pa, side, qty in cases:
            for price in self._PRICES:
                for mult in self._MULTS:
                    nb = compute_fill_nb(ps, pa, side, qty, price, mult)
                    py = _fill.compute_fill(ps, pa, side, qty, price, mult)
                    _assert_match(nb, py, f"flip_long_to_short ps={ps} pa={pa} q={qty} p={price}")
                    assert nb[0] == KIND_FLIP

    def test_flip_short_to_long(self):
        """Buy more than held short — new long position opened at fill price."""
        cases = [
            (-5.0,  100.0, +1, 8.0),
            (-1.0,  200.0, +1, 10.0),
            (-10.0, 50_000.0, +1, 15.0),
        ]
        for ps, pa, side, qty in cases:
            for price in self._PRICES:
                for mult in self._MULTS:
                    nb = compute_fill_nb(ps, pa, side, qty, price, mult)
                    py = _fill.compute_fill(ps, pa, side, qty, price, mult)
                    _assert_match(nb, py, f"flip_short_to_long ps={ps} pa={pa} q={qty} p={price}")
                    assert nb[0] == KIND_FLIP

    # ---- realized PnL correctness (spot-check) ---------------------------

    def test_realized_pnl_long_win(self):
        """Buy 10 @ 100, sell 10 @ 120: pnl = (120-100)*10*mult."""
        for mult in self._MULTS:
            nb = compute_fill_nb(10.0, 100.0, -1, 10.0, 120.0, mult)
            py = _fill.compute_fill(10.0, 100.0, -1, 10.0, 120.0, mult)
            _assert_match(nb, py, f"pnl_long_win mult={mult}")
            expected_pnl = (120.0 - 100.0) * 10.0 * mult
            assert abs(nb[5] - expected_pnl) < _TOL

    def test_realized_pnl_short_win(self):
        """Sell 10 @ 100, cover 10 @ 80: pnl = (80-100)*(-1)*10*mult = +200*mult."""
        for mult in self._MULTS:
            nb = compute_fill_nb(-10.0, 100.0, +1, 10.0, 80.0, mult)
            py = _fill.compute_fill(-10.0, 100.0, +1, 10.0, 80.0, mult)
            _assert_match(nb, py, f"pnl_short_win mult={mult}")
            expected_pnl = (80.0 - 100.0) * (-1.0 * 10.0) * mult
            assert abs(nb[5] - expected_pnl) < _TOL

    def test_realized_pnl_partial_reduce(self):
        """Long 10 @ 100, sell 4 @ 110: pnl = (110-100)*4*mult = 40*mult."""
        for mult in self._MULTS:
            nb = compute_fill_nb(10.0, 100.0, -1, 4.0, 110.0, mult)
            py = _fill.compute_fill(10.0, 100.0, -1, 4.0, 110.0, mult)
            _assert_match(nb, py, f"pnl_partial mult={mult}")

    # ---- avg-price cost-basis correctness --------------------------------

    def test_avg_price_weighted_average(self):
        """Add to long: avg = (prior_avg*prior_qty + fill_price*fill_qty) / total."""
        nb = compute_fill_nb(4.0, 100.0, +1, 6.0, 110.0, 1.0)
        py = _fill.compute_fill(4.0, 100.0, +1, 6.0, 110.0, 1.0)
        _assert_match(nb, py, "avg_price_wacc")
        expected_avg = (100.0 * 4.0 + 110.0 * 6.0) / 10.0
        assert abs(nb[2] - expected_avg) < _TOL

    # ---- portion field correctness ---------------------------------------

    def test_portion_partial_reduce(self):
        """Reduce 3 of 10: portion = 3/10 = 0.3."""
        nb = compute_fill_nb(10.0, 100.0, -1, 3.0, 105.0, 1.0)
        py = _fill.compute_fill(10.0, 100.0, -1, 3.0, 105.0, 1.0)
        _assert_match(nb, py, "portion_partial")
        assert abs(nb[6] - 0.3) < _TOL

    def test_portion_full_close(self):
        """Close all 10 of 10: portion = 1.0."""
        nb = compute_fill_nb(10.0, 100.0, -1, 10.0, 105.0, 1.0)
        py = _fill.compute_fill(10.0, 100.0, -1, 10.0, 105.0, 1.0)
        _assert_match(nb, py, "portion_full_close")
        assert abs(nb[6] - 1.0) < _TOL

    def test_portion_flip(self):
        """Flip long 5 with sell 8: portion = 5/5 = 1.0."""
        nb = compute_fill_nb(5.0, 100.0, -1, 8.0, 105.0, 1.0)
        py = _fill.compute_fill(5.0, 100.0, -1, 8.0, 105.0, 1.0)
        _assert_match(nb, py, "portion_flip")
        assert abs(nb[6] - 1.0) < _TOL

    # ---- leftover field correctness in flip ------------------------------

    def test_leftover_flip(self):
        """Flip long 5 sell 8: leftover = 8-5 = 3."""
        nb = compute_fill_nb(5.0, 100.0, -1, 8.0, 105.0, 1.0)
        py = _fill.compute_fill(5.0, 100.0, -1, 8.0, 105.0, 1.0)
        _assert_match(nb, py, "leftover_flip")
        assert abs(nb[7] - 3.0) < _TOL

    def test_leftover_zero_for_close(self):
        """No leftover on a full close."""
        nb = compute_fill_nb(5.0, 100.0, -1, 5.0, 105.0, 1.0)
        py = _fill.compute_fill(5.0, 100.0, -1, 5.0, 105.0, 1.0)
        _assert_match(nb, py, "leftover_close")
        assert nb[7] == 0.0

    # ---- kind_int / FILL_KIND_NAMES round-trip --------------------------

    def test_kind_enum_roundtrip(self):
        """FILL_KIND_NAMES and FILL_KIND_INTS are consistent inverse mappings."""
        for ki, name in FILL_KIND_NAMES.items():
            assert FILL_KIND_INTS[name] == ki
        assert set(FILL_KIND_NAMES) == {KIND_OPEN, KIND_ADD, KIND_REDUCE, KIND_CLOSE, KIND_FLIP}
