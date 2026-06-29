"""P0 Slice D(c): live Account.balance seeding + AccountState routing tests.

Covers:
- apply_snapshot with balance > 0 seeds Account.balance (not 0)
- apply_snapshot with default balance=0.0 leaves Account.balance at 0.0 (additive, no regressions)
- equity_now() = balance + unrealized_pnl (not PnL-from-zero) when balance is seeded
- AccountState event routed through _on_event updates Account.balance (USDT key)
- AccountState quote-asset fallback: single-asset wallet, no match
- AccountState with empty balances is a no-op
- AccountState for a DIFFERENT venue is ignored (not this hub's account)
- apply_account_state quote-asset selection: match, single-asset fallback, sum fallback
"""
from __future__ import annotations

import pytest

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.crypto_client import ReconcileSnapshot
from vike_trader_app.exec.events import AccountState, FillEvent
from vike_trader_app.exec.live_oms import LiveOmsHub
from vike_trader_app.exec.risk import RiskGate, RiskLimits


# --- shared test helpers -----------------------------------------------------------------------

class _Bus:
    def __init__(self): self.published = []
    def subscribe(self, fn): pass
    def unsubscribe(self, fn): pass
    def publish(self, ev): self.published.append(ev)


class _Client:
    def __init__(self): self.submitted = []
    def submit(self, req): self.submitted.append(req)


def _make_hub(account=None, venue="binance", symbol="BTCUSDT"):
    bus = _Bus()
    acc = account or Account()
    gate = RiskGate(RiskLimits())
    client = _Client()
    hub = LiveOmsHub(bus=bus, account=acc, gate=gate, client=client, venue=venue, symbol=symbol)
    return hub, bus, acc


# --- ReconcileSnapshot.balance field -----------------------------------------------------------

def test_reconcile_snapshot_balance_defaults_zero():
    """Additive field: existing callers that don't set balance get 0.0."""
    snap = ReconcileSnapshot(positions=(("BTCUSDT", 0.5),))
    assert snap.balance == 0.0


def test_reconcile_snapshot_balance_can_be_set():
    snap = ReconcileSnapshot(positions=(("BTCUSDT", 0.5),), balance=10000.0)
    assert snap.balance == 10000.0


def test_reconcile_snapshot_is_still_frozen_hashable():
    snap = ReconcileSnapshot(positions=(("BTCUSDT", 0.5),), balance=5000.0)
    hash(snap)  # must not raise


# --- apply_snapshot balance seeding -----------------------------------------------------------

def test_apply_snapshot_seeds_balance():
    """Core fix: a snapshot with balance > 0 must seed account.balance."""
    hub, _bus, acc = _make_hub()
    snap = ReconcileSnapshot(
        positions=(("BTCUSDT", 0.1),),
        position_avg_px=(("BTCUSDT", 50000.0),),
        balance=12345.67,
    )
    hub.apply_snapshot(snap)
    assert acc.balance == 12345.67


def test_apply_snapshot_zero_balance_leaves_account_unchanged():
    """Default balance=0.0 must NOT overwrite an existing account.balance (e.g. from prior fills)."""
    hub, _bus, acc = _make_hub()
    acc.balance = 999.0  # simulate balance set by a prior fill commission netting
    snap = ReconcileSnapshot(
        positions=(("BTCUSDT", 0.1),),
        position_avg_px=(("BTCUSDT", 50000.0),),
        # balance=0.0 default
    )
    hub.apply_snapshot(snap)
    assert acc.balance == 999.0  # must be untouched


def test_apply_snapshot_balance_additive_no_regression():
    """Existing callers that pass NO balance field still get snapshot behaviour (positions seeded)."""
    hub, _bus, acc = _make_hub()
    snap = ReconcileSnapshot(
        positions=(("BTCUSDT", 2.0),),
        position_avg_px=(("BTCUSDT", 40000.0),),
    )
    hub.apply_snapshot(snap)
    pos = acc.positions.get(("binance", "BTCUSDT", "BOTH"))
    assert pos is not None
    assert pos["size"] == 2.0
    assert pos["avg_px"] == 40000.0


# --- equity_now reflects seeded balance -------------------------------------------------------

def test_equity_now_uses_seeded_balance_not_pnl_from_zero():
    """After apply_snapshot with a balance, equity_now() = balance + unrealized (not PnL-from-zero).

    Before the fix, balance stayed 0.0, so equity_now() equalled only unrealized PnL.
    After the fix: equity = 10000 (wallet) + (55000 - 50000) * 0.1 (unrealized) = 10500.
    """
    hub, _bus, acc = _make_hub()
    snap = ReconcileSnapshot(
        positions=(("BTCUSDT", 0.1),),
        position_avg_px=(("BTCUSDT", 50000.0),),
        position_mark_px=(("BTCUSDT", 55000.0),),
        balance=10000.0,
    )
    hub.apply_snapshot(snap)

    # Check direct account state
    assert acc.balance == 10000.0
    unrealized = acc.unrealized_pnl("binance", "BTCUSDT")
    assert unrealized == pytest.approx((55000.0 - 50000.0) * 0.1)  # 500.0

    # Simulate LiveEngine.equity_now() manually (it reads account.balance + unrealized)
    equity = acc.balance + unrealized
    assert equity == pytest.approx(10500.0)  # not 500.0 (PnL-from-zero)


def test_equity_now_flat_account_with_balance_is_just_balance():
    """A flat position: equity_now() should equal the seeded wallet balance."""
    hub, _bus, acc = _make_hub()
    snap = ReconcileSnapshot(
        positions=(("BTCUSDT", 0.0),),
        balance=25000.0,
    )
    hub.apply_snapshot(snap)
    # unrealized_pnl on a flat position is 0.0
    equity = acc.balance + acc.unrealized_pnl("binance", "BTCUSDT")
    assert equity == pytest.approx(25000.0)


# --- AccountState routing via _on_event -------------------------------------------------------

def test_account_state_event_updates_balance_via_on_event():
    """An AccountState published to the bus must update Account.balance through _on_event."""
    hub, bus, acc = _make_hub(venue="bybit")
    ev = AccountState(venue="bybit", balances=(("USDT", 8888.0),), ts=1)
    bus.subscribe(hub._on_event)  # direct subscription bypass (hub is already subscribed internally)
    hub._on_event(ev)
    assert acc.balance == 8888.0


def test_account_state_wrong_venue_ignored():
    """AccountState for a DIFFERENT venue must be ignored — not this hub's account."""
    hub, _bus, acc = _make_hub(venue="binance")
    acc.balance = 100.0
    ev = AccountState(venue="okx", balances=(("USDT", 9999.0),), ts=1)
    hub._on_event(ev)
    assert acc.balance == 100.0  # untouched


def test_account_state_updates_override_prior_balance():
    """Successive AccountState events from the same venue override (not accumulate) balance."""
    hub, _bus, acc = _make_hub(venue="okx")
    hub._on_event(AccountState(venue="okx", balances=(("USDT", 5000.0),), ts=1))
    assert acc.balance == 5000.0
    hub._on_event(AccountState(venue="okx", balances=(("USDT", 4800.0),), ts=2))
    assert acc.balance == 4800.0  # authoritative override


# --- apply_account_state unit tests (quote-asset selection logic) ------------------------------

def test_apply_account_state_picks_quote_asset_usdt():
    """Match on the 'USDT' (default quote_asset) pair."""
    acc = Account()
    ev = AccountState(venue="binance", balances=(("BTC", 0.5), ("USDT", 7500.0)), ts=0)
    acc.apply_account_state(ev, quote_asset="USDT")
    assert acc.balance == 7500.0


def test_apply_account_state_picks_custom_quote_asset():
    """When quote_asset='BUSD', pick BUSD even if USDT is present."""
    acc = Account()
    ev = AccountState(venue="binance", balances=(("USDT", 1000.0), ("BUSD", 3000.0)), ts=0)
    acc.apply_account_state(ev, quote_asset="BUSD")
    assert acc.balance == 3000.0


def test_apply_account_state_single_asset_fallback():
    """If no quote-asset match but only one balance, use it (e.g. BTC-margined account)."""
    acc = Account()
    ev = AccountState(venue="deribit", balances=(("BTC", 0.25),), ts=0)
    acc.apply_account_state(ev, quote_asset="USDT")
    assert acc.balance == 0.25


def test_apply_account_state_sum_fallback_no_match_multiple():
    """No quote-asset match and >1 balances: sum all."""
    acc = Account()
    ev = AccountState(venue="binance", balances=(("ETH", 2.0), ("BNB", 10.0)), ts=0)
    acc.apply_account_state(ev, quote_asset="USDT")
    assert acc.balance == pytest.approx(12.0)


def test_apply_account_state_empty_balances_noop():
    """Empty balances tuple must NOT zero out an existing balance."""
    acc = Account()
    acc.balance = 500.0
    ev = AccountState(venue="binance", balances=(), ts=0)
    acc.apply_account_state(ev)
    assert acc.balance == 500.0  # untouched


def test_apply_account_state_sets_authoritatively_not_additive():
    """apply_account_state is an absolute SET, not += — call twice, get the last value."""
    acc = Account()
    ev1 = AccountState(venue="x", balances=(("USDT", 1000.0),), ts=0)
    ev2 = AccountState(venue="x", balances=(("USDT", 2000.0),), ts=1)
    acc.apply_account_state(ev1)
    acc.apply_account_state(ev2)
    assert acc.balance == 2000.0  # not 3000


# --- regression: existing snapshot tests still pass (positions/marks/orders unaffected) --------

def test_snapshot_positions_and_marks_unaffected_by_balance_field():
    """The balance field must not disturb position or mark seeding — fully additive."""
    hub, _bus, acc = _make_hub()
    snap = ReconcileSnapshot(
        positions=(("BTCUSDT", 1.5),),
        position_avg_px=(("BTCUSDT", 42000.0),),
        position_mark_px=(("BTCUSDT", 43000.0),),
        balance=20000.0,
    )
    hub.apply_snapshot(snap)
    pos = acc.positions.get(("binance", "BTCUSDT", "BOTH"))
    assert pos["size"] == 1.5
    assert pos["avg_px"] == 42000.0
    assert acc.marks.get(("binance", "BTCUSDT")) == 43000.0
    assert acc.balance == 20000.0
