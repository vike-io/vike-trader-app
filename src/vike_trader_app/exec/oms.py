"""OmsHub — the execution composition root (paper stage).

Owns and wires ONE order->risk->fill->account path: a `BacktestEngine` (orders routed through the
`OrderRouter` + an optional `RiskGate`), a `SimulatedExecutionClient` (engine fills -> `FillEvent`s on
the `EventBus`), and an `Account` read-model (folds the `FillEvent` stream into positions + realized
PnL). At the paper stage it wraps a `PaperTester` (same warm-up / live-bar / result / store semantics),
so it is a drop-in for the GUI forward-tester. The live venue clients (Phase 3b) plug into this exact
shape by swapping the `ExecutionClient` and adding the async worker lifecycle. Qt-free — the GUI owns a
thin QObject around this.

The `SimulatedExecutionClient` is attached to the engine AFTER the `PaperTester`'s seed warm-up, so the
`Account` sees only fills EXECUTED on live bars. (Boundary: an order a strategy submits on the LAST seed
bar fills at the first live open — that fill IS counted live; the cut is "fills executed during warm-up",
not "all seed-period activity".) Three consequences a consumer must respect:
- **Reconcile against the LIVE portion only.** `account.trades` reconciles with
  `result().trades[seed_trade_count:]`, NOT all of `result().trades`: a round-trip CLOSED during warm-up
  is recorded in the engine's trades but the Account never saw it. `seed_trade_count` exposes that split.
- **`.account` is CURVE-ALIGNED, not a position-of-record.** A position opened during warm-up and still
  held when live bars begin is NOT in `.account` (the engine holds it; the Account starts flat). The
  Phase-3b live path must reconcile/seed the Account from the engine's open position before treating it
  as the venue position read-model.
- **`.account` tracks FILLS, not cash.** Non-fill cash moves do not appear in it: a perp FUNDING charge
  moves engine cash with no fill (so `.account`-derived PnL diverges from `result().final_equity` whenever
  funding is present), whereas a LIQUIDATION routes through the fill path so its fill DOES reach the Account.
"""

from __future__ import annotations

from vike_trader_app.core.paper import PaperTester
from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import FillEvent
from vike_trader_app.exec.sim_client import SimulatedExecutionClient


class OmsHub:
    """Paper-stage composition root. Drive it with `on_bar_live`; read `result()` and `.account`."""

    def __init__(self, *, symbol: str, interval: str, strategy, cash: float = 10_000.0,
                 fee_rate: float = 0.0, slippage: float = 0.0, maker_fee=None, taker_fee=None,
                 seed_bars=None, timeframes=None, store=None, on_step=None, created_ts: int = 0,
                 risk=None, venue: str = "sim", _persist: bool = True) -> None:
        self.bus = EventBus()
        self.tester = PaperTester(
            symbol=symbol, interval=interval, strategy=strategy, cash=cash, fee_rate=fee_rate,
            slippage=slippage, maker_fee=maker_fee, taker_fee=taker_fee, seed_bars=seed_bars,
            timeframes=timeframes, store=store, on_step=on_step, created_ts=created_ts,
            risk=risk, _persist=_persist,
        )
        self.account = Account(multiplier=self.tester.engine.multiplier)
        self.bus.subscribe(self._on_event)
        # The Account attaches AFTER warm-up, so trades the engine recorded during the seed are NOT in it;
        # the live portion that reconciles with the Account is result().trades[seed_trade_count:].
        self._seed_trade_count = len(self.tester.engine.trades)
        self.client = SimulatedExecutionClient(self.tester.engine, self.bus, venue=venue, symbol=symbol)

    def _on_event(self, event) -> None:
        if isinstance(event, FillEvent):
            self.account.apply_fill(event)

    # --- duck-compatible with PaperTester (the GUI drives these) ---
    def on_bar_live(self, bar):
        return self.tester.on_bar_live(bar)

    def result(self):
        return self.tester.result()

    def stop(self) -> None:
        self.tester.stop()

    @property
    def run_id(self):
        return self.tester.run_id

    @property
    def engine(self):
        return self.tester.engine

    @property
    def seed_trade_count(self) -> int:
        """How many engine trades were recorded during seed warm-up (before the live Account attached).
        `account.trades` reconciles with `result().trades[seed_trade_count:]`, not the full list."""
        return self._seed_trade_count

    @property
    def live_trades(self):
        """The live portion of `result().trades` — the slice that reconciles with `account.trades`."""
        return self.result().trades[self._seed_trade_count:]

    @property
    def equity_curve(self):
        return self.tester.equity_curve
