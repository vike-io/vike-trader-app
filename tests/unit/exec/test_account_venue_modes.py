"""S1: per-venue + per-symbol-multiplier Account with explicit balance_mode.

Pins the FINAL post-S1 Account surface (contract A): keyword-only venue/multipliers/balance_mode,
multiplier_of() with the legacy scalar as the unlisted default, equity_all() branching on balance_mode,
the venue assert in apply_fill, and _fold() as the sole position/realized writer.
"""

from __future__ import annotations

import pytest

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.events import AccountState, FillEvent, FundingEvent, PositionLiquidated

TOL = 1e-10


def _fill(*, venue="sim", symbol="X", side=1, qty=1.0, px=100.0, commission=0.0, ts=0):
    return FillEvent(
        trade_id="t", client_order_id="c", venue=venue, symbol=symbol,
        side=side, last_qty=qty, last_px=px, commission=commission, ts=ts,
    )


# --- ctor surface + back-compat ------------------------------------------------------------------

def test_default_ctor_is_sim_venue_delta_mode():
    acc = Account()
    assert acc.venue == "sim"
    assert acc.balance_mode == "delta"
    assert acc.multiplier_of("X") == 1.0
    assert acc.multiplier_of("ANY") == 1.0
    assert not hasattr(acc, "multiplier")  # scalar attr REMOVED (contract A)


def test_legacy_scalar_multiplier_is_unlisted_default():
    # oms.py:54 calls Account(multiplier=K) -> multiplier_of(any) == K.
    acc = Account(multiplier=5.0)
    assert acc.multiplier_of("X") == 5.0
    assert acc.multiplier_of("BTCUSDT") == 5.0


def test_per_symbol_multipliers_override_legacy_default():
    acc = Account(multiplier=2.0, multipliers={"ETHUSDT": 10.0, "BTCUSDT": 50.0})
    assert acc.multiplier_of("ETHUSDT") == 10.0
    assert acc.multiplier_of("BTCUSDT") == 50.0
    assert acc.multiplier_of("UNLISTED") == 2.0  # falls back to legacy default


def test_venue_kwarg_recorded():
    acc = Account(venue="binance")
    assert acc.venue == "binance"


# --- equity_all: delta mode -----------------------------------------------------------------------

def test_equity_all_delta_open_position():
    acc = Account(venue="sim")
    acc.apply_fill(_fill(side=1, qty=2.0, px=100.0))   # buy 2 @ 100, no fee
    acc.set_mark("sim", "X", 110.0)
    # delta: seed + balance + realized + unrealized.  balance=0 (no fee), realized=0,
    # unrealized = (110-100)*2*1 = 20.  seed=1000.
    assert acc.equity_all(1000.0) == pytest.approx(1000.0 + 0.0 + 0.0 + 20.0, abs=TOL)


def test_equity_all_delta_matches_equity_method_single_symbol():
    # equity_all(seed) in delta mode must equal the legacy equity(initial_cash) for the single-symbol case.
    acc = Account(venue="sim")
    acc.apply_fill(_fill(side=1, qty=1.0, px=100.0, commission=0.5))
    acc.set_mark("sim", "X", 105.0)
    legacy = acc.equity(1000.0, venue="sim", symbol="X", position_side="BOTH")
    assert acc.equity_all(1000.0) == pytest.approx(legacy, abs=TOL)


def test_equity_all_delta_sums_multiple_symbols_with_own_multipliers():
    acc = Account(venue="sim", multipliers={"A": 1.0, "B": 10.0})
    acc.apply_fill(_fill(symbol="A", side=1, qty=1.0, px=100.0))
    acc.apply_fill(_fill(symbol="B", side=1, qty=1.0, px=50.0))
    acc.set_mark("sim", "A", 110.0)   # +10 * 1
    acc.set_mark("sim", "B", 52.0)    # +2 * 10 = +20
    assert acc.equity_all(0.0) == pytest.approx(0.0 + 0.0 + 0.0 + 10.0 + 20.0, abs=TOL)


# --- equity_all: authoritative mode (set by apply_account_state) ----------------------------------

def test_apply_account_state_flips_to_authoritative_and_drops_seed_and_realized():
    acc = Account(venue="binance")
    acc.apply_fill(_fill(venue="binance", side=1, qty=1.0, px=100.0))  # opens a position
    acc.set_mark("binance", "X", 110.0)                                # +10 unrealized
    acc.apply_account_state(AccountState(venue="binance", balances=(("USDT", 5000.0),)))
    assert acc.balance_mode == "authoritative"
    assert acc.balance == 5000.0
    # authoritative: balance + unrealized only (NO seed, NO realized re-add).
    assert acc.equity_all(999_999.0) == pytest.approx(5000.0 + 10.0, abs=TOL)


# --- venue assert ---------------------------------------------------------------------------------

def test_apply_fill_rejects_foreign_venue():
    acc = Account(venue="sim")
    with pytest.raises(AssertionError):
        acc.apply_fill(_fill(venue="binance"))


# --- _fold is the sole writer: apply_fill and apply_liquidation realize identically ---------------

def test_fold_realizes_on_liquidation_like_a_fill():
    acc = Account(venue="sim")
    acc.apply_fill(_fill(side=1, qty=2.0, px=100.0))   # long 2 @ 100
    acc.apply_liquidation(PositionLiquidated(
        venue="sim", symbol="X", position_side="BOTH", qty=2.0, liq_price=90.0, fee=1.0,
    ))
    assert acc.positions[("sim", "X", "BOTH")]["size"] == pytest.approx(0.0, abs=TOL)
    # realized = (90-100)*2*1 = -20 ; balance -= fee(1.0)
    assert acc.realized_pnl == pytest.approx(-20.0, abs=TOL)
    assert acc.trades[-1] == pytest.approx(-20.0, abs=TOL)
    assert acc.balance == pytest.approx(-1.0, abs=TOL)
