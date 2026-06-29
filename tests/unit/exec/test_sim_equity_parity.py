"""Rung-3 acceptance gate (Task C2): Account equity == engine equity, bar-by-bar.

The equity identity verified per bar:

    initial_cash
    + Account.balance          (= cumulative -commission, no notional line)
    + Account.realized_pnl     (gross price PnL of all closed portions)
    + Account.unrealized_pnl("sim", "X", "BOTH")
                               (= (mark - avg_px) * size * multiplier, 0.0 if flat)
    ==
    engine.equity_now()        (= cash + position.size * _price * multiplier)

Why it holds bar-by-bar:
  (a) FillEvent.commission == engine._fee: the _on_fill hook passes `fee` (the exact
      value deducted from engine.cash) as FillEvent.commission -> Account.balance
      nets the same cash out.
  (b) Account.set_mark("sim", "X", bar.close) is called each bar, matching engine._price
      (which is set to bar.close in _advance) -> unrealized uses the same mark.
  (c) Account.realized_pnl is derived by compute_fill with the same arguments as the
      engine -> gross price PnL is bit-for-bit equal.
  (d) Account.balance does NOT carry the notional (no "cash -= delta * price * mult"
      line). The notional cancels inside equity: realized_pnl encodes
      (price - avg_px)*size*mult for closed portions, unrealized_pnl encodes
      (mark - avg_px)*size*mult for open portions. Together with the -commission
      balance they reproduce equity_now() exactly.

FUNDING CAVEAT:
    The engine computes funding as `cash -= funding_charge(pos.size, close, rate, mult)`
    inside `_advance`. SimulatedExchange (C1b) does NOT currently emit a FundingEvent
    for this cash deduction. Therefore the equity identity breaks when funding is non-zero:
    Account.balance is missing the funding cashflow that engine.cash absorbed.

    Scope: funding parity is a SLICE-D responsibility. The funding scenario below is
    explicitly marked xfail with the exact failure mode described. When Slice D adds
    FundingEvent emission from the SimulatedExchange, change xfail -> xpass.

    The non-funding scenarios (all 4 strategy scenarios + slippage/multiplier +
    liquidation) MUST all pass.

TOL = 1e-10 (absolute).
    Chosen because all arithmetic is float64 with a small number of operations per bar
    (< ~10 multiplications/additions); double-precision accumulation error is at most
    a few ULPs at these magnitudes (prices ~100, cash ~10000). 1e-10 is 10x tighter
    than the 1e-9 bracket-partial tolerance used in sim_exchange, leaving headroom
    for the few extra operations here without false passes.
"""

from __future__ import annotations

import pytest

from vike_trader_app.core.engine import SingleSymbolEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import FillEvent, FundingEvent
from vike_trader_app.exec.sim_exchange import SimulatedExchange


# ---------------------------------------------------------------------------
# Tolerance
# ---------------------------------------------------------------------------

TOL = 1e-10  # absolute; see module docstring for rationale


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 5, low=min(o, c) - 5, close=c, volume=1.0)


def _ramp():
    closes = [100, 110, 120, 130, 120, 110, 100, 110]
    return [_bar(i * 60_000, c, c) for i, c in enumerate(closes)]


def _account_equity(initial_cash: float, acc: Account) -> float:
    """Delegate to Account.equity() — DRY shortcut used by the test harness."""
    return acc.equity(initial_cash, venue="sim", symbol="X", position_side="BOTH")


def _run_bar_by_bar(strat, bars, *, initial_cash: float, fee: float = 0.0,
                    slippage: float = 0.0, multiplier: float = 1.0,
                    leverage=None, maint_margin: float = 0.0,
                    cashflows=None) -> tuple[Account, SingleSymbolEngine, list[float], list[float]]:
    """Step bar-by-bar; after each bar call set_mark and record equity pair.

    Returns (acc, eng, eng_equities, acc_equities).
    """
    bus = EventBus()
    acc = Account(multiplier=multiplier)
    bus.subscribe(lambda ev: acc.apply_fill(ev) if isinstance(ev, FillEvent) else None)
    bus.subscribe(lambda ev: acc.apply_funding(ev) if isinstance(ev, FundingEvent) else None)

    eng = SingleSymbolEngine(
        bars, strat,
        cash=initial_cash,
        taker_fee=fee,
        slippage=slippage,
        multiplier=multiplier,
        leverage=leverage,
        maint_margin=maint_margin if maint_margin else 0.0,
        cashflows=cashflows,
    )
    SimulatedExchange(eng, bus, venue="sim", symbol="X")

    eng_equities = []
    acc_equities = []

    eng.strategy.on_start()
    for i, bar in enumerate(bars):
        eng_eq = eng.step(bar, i)
        acc.set_mark("sim", "X", bar.close)
        acc_eq = _account_equity(initial_cash, acc)
        eng_equities.append(eng_eq)
        acc_equities.append(acc_eq)
    eng.strategy.on_stop()

    return acc, eng, eng_equities, acc_equities


# ---------------------------------------------------------------------------
# Strategy classes (same scenarios as test_sim_parity.py / test_sim_exchange.py)
# ---------------------------------------------------------------------------

class _BuyThenClose(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 3:
            self.close()


class _ScaleInThenOut(Strategy):
    def on_bar(self, bar):
        if self.index in (0, 1):
            self.buy(1.0)
        elif self.index == 4:
            self.close()


class _LongThenFlipShort(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 3:
            self.order_target_shares(-1.0)
        elif self.index == 6:
            self.close()


class _ShortThenCover(Strategy):
    def on_bar(self, bar):
        if self.index == 1:
            self.sell(2.0)
        elif self.index == 5:
            self.close()


# ---------------------------------------------------------------------------
# Core: 4 golden strategy scenarios
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("strat_cls", [
    _BuyThenClose,
    _ScaleInThenOut,
    _LongThenFlipShort,
    _ShortThenCover,
])
def test_equity_parity_golden_scenarios(strat_cls):
    """Account equity == engine equity EVERY bar for all 4 golden strategies."""
    initial_cash = 10_000.0
    _, eng, eng_equities, acc_equities = _run_bar_by_bar(
        strat_cls(), _ramp(),
        initial_cash=initial_cash, fee=0.001,
    )
    assert len(eng_equities) == len(_ramp())  # sanity: all bars processed
    for i, (eng_eq, acc_eq) in enumerate(zip(eng_equities, acc_equities)):
        assert acc_eq == pytest.approx(eng_eq, abs=TOL), (
            f"{strat_cls.__name__}: bar {i}: "
            f"engine={eng_eq}, account={acc_eq}, diff={acc_eq - eng_eq}"
        )


# ---------------------------------------------------------------------------
# Slippage + multiplier
# ---------------------------------------------------------------------------

def test_equity_parity_slippage_and_multiplier():
    """Equity identity holds with non-zero slippage and multiplier > 1."""
    initial_cash = 100_000.0
    _, _, eng_equities, acc_equities = _run_bar_by_bar(
        _BuyThenClose(), _ramp(),
        initial_cash=initial_cash, fee=0.001, slippage=0.0005, multiplier=5.0,
    )
    for i, (eng_eq, acc_eq) in enumerate(zip(eng_equities, acc_equities)):
        assert acc_eq == pytest.approx(eng_eq, abs=TOL), (
            f"slippage+multiplier bar {i}: engine={eng_eq}, account={acc_eq}"
        )


# ---------------------------------------------------------------------------
# Leverage + liquidation scenario
# ---------------------------------------------------------------------------

class _LeveragedLong(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(100.0)  # leverage cap trims to max allowed


def _liquidation_bars():
    return [
        Bar(ts=0,        open=100, high=105, low=98,  close=100, volume=1.0),
        Bar(ts=60_000,   open=100, high=102, low=95,  close=100, volume=1.0),
        Bar(ts=120_000,  open=100, high=102, low=90,  close=100, volume=1.0),
    ]


def test_equity_parity_liquidation():
    """Equity identity holds through the liquidation force-close bar.

    The liquidation fill goes through _apply_fill -> _on_fill hook ->
    bare FillEvent -> Account.apply_fill, so Account sees the closing fill
    and realizes the PnL. Equity parity must hold on EVERY bar including
    the liquidation bar.
    """
    initial_cash = 1_000.0
    _, eng, eng_equities, acc_equities = _run_bar_by_bar(
        _LeveragedLong(), _liquidation_bars(),
        initial_cash=initial_cash, fee=0.0,
        leverage=10.0, maint_margin=0.05,
    )
    assert eng.position.size == 0.0  # liquidated -> flat
    for i, (eng_eq, acc_eq) in enumerate(zip(eng_equities, acc_equities)):
        assert acc_eq == pytest.approx(eng_eq, abs=TOL), (
            f"liquidation bar {i}: engine={eng_eq}, account={acc_eq}"
        )


# ---------------------------------------------------------------------------
# Bracket scenario (SL+TP intrabar cap)
# ---------------------------------------------------------------------------

class _BracketStrategy(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 1:
            self.stop_sell(1.0, price=95.0)
            self.limit_sell(1.0, price=115.0)


def _bracket_bars():
    return [
        Bar(ts=0,        open=100, high=105, low=95,  close=102, volume=1.0),
        Bar(ts=60_000,   open=100, high=105, low=95,  close=100, volume=1.0),
        Bar(ts=120_000,  open=100, high=120, low=90,  close=105, volume=1.0),
    ]


def test_equity_parity_bracket():
    """Equity identity holds through the adversarial SL+TP bracket cap scenario."""
    initial_cash = 10_000.0
    _, _, eng_equities, acc_equities = _run_bar_by_bar(
        _BracketStrategy(), _bracket_bars(),
        initial_cash=initial_cash, fee=0.0,
    )
    for i, (eng_eq, acc_eq) in enumerate(zip(eng_equities, acc_equities)):
        assert acc_eq == pytest.approx(eng_eq, abs=TOL), (
            f"bracket bar {i}: engine={eng_eq}, account={acc_eq}"
        )


# ---------------------------------------------------------------------------
# FUNDING CAVEAT — xfail (Slice D)
# ---------------------------------------------------------------------------

class _HoldStrategy(Strategy):
    """Simply opens a position and holds indefinitely."""
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)


def _funding_bars():
    """4 bars with a non-zero funding rate on bars 1-3."""
    # funding is stored on the Bar; a positive funding rate means longs pay.
    return [
        Bar(ts=0,       open=100, high=105, low=95, close=100, volume=1.0, funding=None),
        Bar(ts=60_000,  open=100, high=105, low=95, close=100, volume=1.0, funding=0.0001),
        Bar(ts=120_000, open=100, high=105, low=95, close=100, volume=1.0, funding=0.0001),
        Bar(ts=180_000, open=100, high=105, low=95, close=100, volume=1.0, funding=0.0001),
    ]


def test_equity_parity_funding():
    """Equity identity holds with funding: FundingEvent emitted from engine → Account.apply_funding.

    Slice D (feat/p0d-funding-account-equity): SimulatedExchange now wires _on_funding and publishes
    FundingEvent(amount=-funding_charge). Account.apply_funding folds it (balance += ev.amount),
    so Account.balance mirrors the same signed cash delta as engine.cash. Parity is restored.
    """
    initial_cash = 10_000.0
    _, _, eng_equities, acc_equities = _run_bar_by_bar(
        _HoldStrategy(), _funding_bars(),
        initial_cash=initial_cash, fee=0.0,
    )
    for i, (eng_eq, acc_eq) in enumerate(zip(eng_equities, acc_equities)):
        assert acc_eq == pytest.approx(eng_eq, abs=TOL), (
            f"funding bar {i}: engine={eng_eq}, account={acc_eq}, diff={acc_eq - eng_eq}"
        )
