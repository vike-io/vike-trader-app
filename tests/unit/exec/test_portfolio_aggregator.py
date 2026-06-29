"""S2: Portfolio aggregator — pure offline read-model over per-venue Accounts.

Portfolio aggregates per-venue ``Account``s (the S1 contract). It owns no fill
logic: callers feed each Account (apply_fill / set_mark / apply_account_state),
the Portfolio just sums. ``equity()`` is correct in BOTH balance modes because
each Account branches on its own ``balance_mode`` inside ``equity_all(seed)``.

TOL = 1e-10 (absolute) — same float64 budget as the C2 equity-parity gate.
"""

from __future__ import annotations

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.events import AccountState, FillEvent
from vike_trader_app.exec.portfolio import Portfolio

TOL = 1e-10


def _fill(venue, symbol, side, qty, px, commission=0.0, ts=0):
    """A FillEvent matching the frozen exec/events.py shape (S1 emit shape)."""
    return FillEvent(
        trade_id=f"{venue}-{symbol}-{ts}",
        client_order_id=f"c-{venue}-{symbol}-{ts}",
        venue=venue,
        symbol=symbol,
        side=side,
        last_qty=qty,
        last_px=px,
        commission=commission,
        liquidity_side="taker",
        ts=ts,
    )


def test_account_lazy_create_and_idempotent():
    pf = Portfolio()
    a = pf.account("binance", multipliers={"BTCUSDT": 1.0}, seed=5_000.0)
    assert a.venue == "binance"
    assert a.balance_mode == "delta"          # Portfolio always creates delta-mode Accounts
    assert pf.seeds["binance"] == 5_000.0
    assert pf.accounts["binance"] is a
    # Second call returns the SAME object, ignoring the new multipliers/seed.
    b = pf.account("binance", multipliers={"ETHUSDT": 7.0}, seed=999.0)
    assert b is a
    assert pf.seeds["binance"] == 5_000.0     # seed unchanged
    assert a.multiplier_of("ETHUSDT") == 1.0  # 2nd-call multipliers ignored (legacy default 1.0)
    # A different venue makes a distinct Account.
    c = pf.account("bybit")
    assert c is not a
    assert c.venue == "bybit"
    assert pf.seeds["bybit"] == 0.0           # default seed


def _drive_single_venue_golden(scenario):
    """Build a 1-venue Portfolio, replay a fill stream + final mark, return (pf, acc, seed)."""
    pf = Portfolio()
    seed = 10_000.0
    acc = pf.account("sim", multipliers={"X": 1.0}, seed=seed)
    fills, final_mark = scenario
    for f in fills:
        acc.apply_fill(f)
    if final_mark is not None:
        acc.set_mark("sim", "X", final_mark)
    return pf, acc, seed


def test_single_venue_equity_equals_account_equity_all():
    # 4 golden position shapes: open-and-hold, add-averaged, reduce, close-and-flip.
    scenarios = [
        ([_fill("sim", "X", +1, 2.0, 100.0, commission=0.2)], 110.0),
        ([_fill("sim", "X", +1, 2.0, 100.0, commission=0.2),
          _fill("sim", "X", +1, 3.0, 120.0, commission=0.3)], 115.0),
        ([_fill("sim", "X", +1, 5.0, 100.0, commission=0.5),
          _fill("sim", "X", -1, 2.0, 130.0, commission=0.2)], 130.0),
        ([_fill("sim", "X", +1, 2.0, 100.0, commission=0.2),
          _fill("sim", "X", -1, 5.0, 90.0, commission=0.5)], 80.0),
    ]
    for scenario in scenarios:
        pf, acc, seed = _drive_single_venue_golden(scenario)
        assert abs(pf.equity() - acc.equity_all(seed)) < TOL
        assert abs(pf.realized() - acc.realized_pnl) < TOL
        assert abs(pf.unrealized() - acc.unrealized_pnl("sim", "X", "BOTH")) < TOL
        assert abs(pf.fees() - acc.fees_paid) < TOL
        assert pf.funding() == acc.funding_paid


def test_two_venue_equity_net_position_and_exposure():
    pf = Portfolio()
    a = pf.account("venueA", multipliers={"BTCUSDT": 1.0}, seed=10_000.0)
    b = pf.account("venueB", multipliers={"BTCUSDT": 1.0}, seed=20_000.0)
    a.apply_fill(_fill("venueA", "BTCUSDT", +1, 2.0, 100.0))
    b.apply_fill(_fill("venueB", "BTCUSDT", -1, 2.0, 105.0))
    a.set_mark("venueA", "BTCUSDT", 110.0)
    b.set_mark("venueB", "BTCUSDT", 110.0)

    assert abs(pf.equity() - (a.equity_all(10_000.0) + b.equity_all(20_000.0))) < TOL
    assert abs(pf.net_position("BTCUSDT") - 0.0) < TOL
    assert pf.net_position("ETHUSDT") == 0.0
    assert abs(pf.exposure() - 440.0) < TOL   # |2|*110*1 + |-2|*110*1


def test_exposure_applies_per_symbol_multiplier():
    pf = Portfolio()
    a = pf.account("sim", multipliers={"BIG": 10.0, "SMALL": 1.0}, seed=0.0)
    a.apply_fill(_fill("sim", "BIG", +1, 3.0, 50.0))     # notional = 3*50*10 = 1500
    a.apply_fill(_fill("sim", "SMALL", -1, 4.0, 25.0))   # notional = 4*25*1  = 100
    a.set_mark("sim", "BIG", 50.0)
    a.set_mark("sim", "SMALL", 25.0)
    assert abs(pf.exposure() - (3.0 * 50.0 * 10.0 + 4.0 * 25.0 * 1.0)) < TOL   # 1600.0
    assert abs(pf.net_position("BIG") - 3.0) < TOL
    assert abs(pf.net_position("SMALL") - (-4.0)) < TOL


def test_authoritative_frame_drops_seed_and_realized_no_double_count():
    pf = Portfolio()
    seedA = 50_000.0
    a = pf.account("live", multipliers={"BTCUSDT": 1.0}, seed=seedA)
    a.apply_fill(_fill("live", "BTCUSDT", +1, 2.0, 100.0, commission=1.0))
    a.apply_fill(_fill("live", "BTCUSDT", -1, 1.0, 130.0, commission=1.0))
    a.set_mark("live", "BTCUSDT", 140.0)
    assert a.realized_pnl != 0.0
    a.apply_account_state(AccountState(venue="live", balances=(("USDT", 49_999.0),), ts=1))
    assert a.balance_mode == "authoritative"
    expected = 49_999.0 + a.unrealized_pnl("live", "BTCUSDT", "BOTH")
    assert abs(pf.equity() - expected) < TOL
    assert abs(pf.equity() - (seedA + 49_999.0)) > 1.0   # discarded seed NOT in equity


def test_venue_breakdown_shape():
    pf = Portfolio()
    a = pf.account("venueA", multipliers={"BTCUSDT": 1.0}, seed=1_000.0)
    a.apply_fill(_fill("venueA", "BTCUSDT", +1, 2.0, 100.0, commission=0.5))
    a.set_mark("venueA", "BTCUSDT", 110.0)
    pf.account("venueB", seed=0.0)   # empty venue still appears

    bd = pf.venue_breakdown()
    assert set(bd) == {"venueA", "venueB"}
    va = bd["venueA"]
    assert set(va) == {"equity", "realized", "unrealized", "fees", "funding", "positions"}
    assert abs(va["equity"] - a.equity_all(1_000.0)) < TOL
    assert abs(va["realized"] - a.realized_pnl) < TOL
    assert abs(va["unrealized"] - a.unrealized_pnl("venueA", "BTCUSDT", "BOTH")) < TOL
    assert abs(va["fees"] - a.fees_paid) < TOL
    assert va["funding"] == a.funding_paid
    assert va["positions"][("BTCUSDT", "BOTH")]["size"] == 2.0
    assert va["positions"][("BTCUSDT", "BOTH")]["avg_px"] == 100.0
    assert bd["venueB"]["positions"] == {}
