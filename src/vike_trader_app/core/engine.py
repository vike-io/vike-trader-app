"""The backtest engine: a bar-at-a-time event loop with next-open fills.

Look-ahead guard: orders submitted during bar *i* fill at the **open of bar i+1**,
because pending orders are filled at the start of each bar *before* the strategy runs.
"""

import bisect
from dataclasses import dataclass
from operator import attrgetter

from .broker_sim import adverse_fill_price, fee as _fee, funding_charge
from .fill import compute_fill
from .model import Bar, Position, Trade
from .fill_model import BarFillModel
from .orders import Order, order_fill_price
from .timeframe import parse_timeframe, resample

_BAR_TS = attrgetter("ts")   # bisect key: higher-TF reads slice a ts-ascending list in O(log n)


@dataclass
class Result:
    """Outcome of a backtest run."""

    trades: list[Trade]
    equity_curve: list[float]
    final_equity: float
    # Count of bars where a stop-loss AND a take-profit (both reducing the position) triggered in the
    # SAME bar — OHLC can't say which hit first, so the engine resolved them pessimistically (stop
    # first). A high count means the headline result leans on that pessimistic assumption.
    intrabar_both_hit: int = 0


class BacktestEngine:
    """Runs a `Strategy` over a list of `Bar`s with simulated fills and fees."""

    def __init__(
        self,
        bars,
        strategy,
        fee_rate: float = 0.0,
        cash: float = 10_000.0,
        timeframes=None,
        slippage: float = 0.0,
        maker_fee: float | None = None,
        taker_fee: float | None = None,
        multiplier: float = 1.0,
        leverage: float | None = None,
        maint_margin: float = 0.0,
        cashflows=None,
        on_fill=None,
        risk=None,
        fill_model=None,
    ) -> None:
        self.bars = bars
        self.strategy = strategy
        self.fee_rate = fee_rate
        # maker = resting limit fills; taker = market/stop/trailing. Fall back to fee_rate.
        self.maker_fee = maker_fee if maker_fee is not None else fee_rate
        self.taker_fee = taker_fee if taker_fee is not None else fee_rate
        self.slippage = slippage
        self.multiplier = multiplier
        self.leverage = leverage
        self.maint_margin = maint_margin
        self._cashflows = cashflows
        self._on_fill = on_fill   # optional: called per fill (side, size, price, fee, ts, is_maker)
        self._fill_model = fill_model if fill_model is not None else BarFillModel()
        self.cash = cash
        self.position = Position()
        self.trades: list[Trade] = []
        self.intrabar_both_hit = 0   # bars where a SL+TP bracket both triggered (resolved stop-first)
        self._pending: list[Order] = []
        self._entry_fee = 0.0
        self._entry_ts = 0
        self._price = bars[0].open if bars else 0.0
        self._now = bars[0].ts if bars else 0
        self._peak = cash  # peak equity, for drawdown
        # Precompute each higher timeframe once: tf -> (target_ms, [coarse bars]).
        self._tf: dict[str, tuple[int, list]] = {}
        for tf in timeframes or []:
            ms = parse_timeframe(tf)
            self._tf[tf] = (ms, resample(bars, ms))
        if risk is not None:
            from .order_router import OrderRouter
            strategy._engine = OrderRouter(self, risk)
        else:
            strategy._engine = self

    # --- order intake (called from the strategy) ---
    def submit(self, side_sign: int, size: float, weight: float = 0.0, stop=None) -> None:
        # stop= is honored only in portfolio mode; the single-symbol engine accepts and ignores it
        # to keep this path (and the numba kernel parity) byte-for-byte unchanged.
        del stop
        size = self._cap_to_leverage(side_sign, size)
        if size > 0.0:
            self._pending.append(Order("market", side_sign, size, weight=weight))

    def _cap_to_leverage(self, side_sign: int, size: float) -> float:
        """Shrink a market order so the resulting position notional <= leverage * equity.

        Accounts for already-pending market orders (so a flip's pending close brings the
        projected position to flat before the new entry is capped — matching the kernel,
        which caps each entry as if opened from flat). Reducing/closing orders are never shrunk.
        """
        if self.leverage is None:
            return size
        eq = self.equity_now()
        if eq <= 0.0:
            return 0.0
        max_pos = (self.leverage * eq) / (self._price * self.multiplier)
        pending = 0.0
        for o in self._pending:
            if o.kind == "market":
                pending += o.side * o.size
        projected = self.position.size + pending
        if (projected >= 0.0) == (side_sign > 0):
            room = max_pos - abs(projected)          # extending the same side (or from flat)
        else:
            room = abs(projected) + max_pos          # reducing or crossing through zero
        if room <= 0.0:
            return 0.0
        return size if size <= room else room

    def submit_limit(self, side_sign: int, size: float, price: float, weight: float = 0.0) -> None:
        self._pending.append(Order("limit", side_sign, size, price=price, weight=weight))

    def submit_stop(self, side_sign: int, size: float, price: float, weight: float = 0.0) -> None:
        self._pending.append(Order("stop", side_sign, size, price=price, weight=weight))

    def submit_trailing(self, side_sign: int, size: float, trail: float, weight: float = 0.0) -> None:
        self._pending.append(Order("trailing", side_sign, size, trail=trail, extreme=self._price, weight=weight))

    def submit_market_close(self, side_sign: int, size: float) -> None:
        self._pending.append(Order("market_close", side_sign, size))

    def submit_limit_close(self, side_sign: int, size: float, price: float) -> None:
        self._pending.append(Order("limit_close", side_sign, size, price=price))

    def cancel_all(self) -> None:
        self._pending = []

    def submit_close(self) -> None:
        if self.position.size != 0:
            side = -1 if self.position.size > 0 else 1
            self._pending.append(Order("market", side, abs(self.position.size)))

    def order_target(self, target_size: float) -> None:
        """Market order to move the position to ``target_size`` signed shares."""
        delta = target_size - self.position.size
        if delta > 0:
            self.submit(+1, delta)
        elif delta < 0:
            self.submit(-1, -delta)

    def order_target_value(self, value: float) -> None:
        self.order_target(value / (self._price * self.multiplier))

    def order_target_percent(self, pct: float) -> None:
        self.order_target(pct * self.equity_now() / (self._price * self.multiplier))

    def equity_now(self) -> float:
        return self.cash + self.position.size * self._price * self.multiplier

    def drawdown_now(self) -> float:
        """Current drawdown from the running equity peak (0.2 == 20% below peak)."""
        eq = self.equity_now()
        return (self._peak - eq) / self._peak if self._peak > 0 else 0.0

    def bars_for(self, tf: str):
        """Completed higher-TF bars visible at the current base bar (deliver-on-complete)."""
        ms, coarse = self._tf[tf]
        window_start = self._now - self._now % ms
        # coarse is ts-ascending: bisect to the first bar at/after window_start instead of rescanning
        # the whole list every bar (was O(n) per call -> O(n^2) per run on a multi-timeframe strategy).
        return coarse[:bisect.bisect_left(coarse, window_start, key=_BAR_TS)]

    def forming_for(self, tf: str):
        """The still-building coarse bar for ``tf`` from base bars seen so far, or None."""
        ms, _ = self._tf[tf]
        window_start = self._now - self._now % ms
        # self.bars is ts-ascending: slice the [window_start, _now] window via bisect rather than
        # scanning the whole base series each call (the dominant MTF O(n^2) hot path).
        lo = bisect.bisect_left(self.bars, window_start, key=_BAR_TS)
        hi = bisect.bisect_right(self.bars, self._now, key=_BAR_TS)
        window = self.bars[lo:hi]
        if not window:
            return None
        return Bar(
            ts=window_start,
            open=window[0].open,
            high=max(b.high for b in window),
            low=min(b.low for b in window),
            close=window[-1].close,
            volume=sum(b.volume for b in window),
        )

    # --- run loop ---
    def run(self) -> Result:
        equity_curve = [self.step(bar, i) for i, bar in enumerate(self.bars)]
        return Result(self.trades, equity_curve, self.equity_now(),
                      intrabar_both_hit=self.intrabar_both_hit)

    def add_live_bar(self, bar: Bar) -> None:
        """Append a live bar to history and refresh higher-TF aggregates (forward mode).

        Call this *before* ``step`` so the strategy's ``bars(tf)``/``forming(tf)`` see the
        same data a backtest would at this bar. Re-resampling each live bar is O(n) but the
        forward cadence is one bar per interval, so it's negligible.
        """
        self.bars.append(bar)
        for tf, (ms, _) in list(self._tf.items()):
            self._tf[tf] = (ms, resample(self.bars, ms))

    def step(self, bar: Bar, i: int) -> float:
        """Advance the engine by exactly one bar; return equity after it.

        Identical to one iteration of ``run`` — the shared primitive the forward
        (paper) loop drives live, so strategies behave the same backtest↔forward.
        Pending orders fill at this bar's open *before* the strategy runs (next-open).
        The strategy is gated until ``i >= strategy.WARMUP`` (never act on NaN).
        """
        self._fill_pending(bar)  # fills before decisions => next-open semantics
        self.strategy.index = i
        self._now = bar.ts
        self._price = bar.close
        if bar.funding is not None and self.position.size != 0:
            self.cash -= funding_charge(self.position.size, bar.close, bar.funding, self.multiplier)
        if self._cashflows is not None:
            self.cash += self._cashflows[i]
        self._check_liquidation(bar)
        self._peak = max(self._peak, self.equity_now())
        if i >= self.strategy.WARMUP:  # warm-up gate: skip until indicators have history
            self.strategy.on_bar(bar)
        return self.equity_now()

    def _fill_pending(self, bar: Bar) -> None:
        triggered: list[tuple[Order, float]] = []
        still: list[Order] = []
        for o in self._pending:
            fill_price = self._fill_model.fill_price(o, bar)
            if fill_price is None:
                still.append(o)  # rest until triggered
            else:
                triggered.append((o, fill_price))
        self._pending = still
        if len(triggered) > 1:
            triggered = self._resolve_intrabar(triggered)
        for o, fill_price in triggered:
            if o.size <= 1e-12:                 # capped to ~0 by the bracket guard -> nothing to fill
                continue
            self._apply_fill(o.side, o.size, fill_price, bar.ts, is_maker=o.kind == "limit")

    def _resolve_intrabar(self, triggered: "list[tuple[Order, float]]") -> "list[tuple[Order, float]]":
        """Several resting orders triggered in ONE bar — OHLC can't reveal the intrabar sequence.

        Apply ADVERSE (stop/trailing) fills before FAVOURABLE (limit) fills (pessimistic ordering).
        When more than one order REDUCES the current position in the same bar (a stop-loss +
        take-profit bracket), cap the total reduction to the position size, adverse-first, so the
        profit target can't also fill after the stop already flattened the position. The ambiguous
        bar is counted in ``intrabar_both_hit`` (surfaced on the Result for honesty)."""
        triggered = sorted(triggered, key=lambda t: 0 if t[0].kind in ("stop", "trailing") else 1)
        pos = self.position.size
        closing_side = -1 if pos > 0 else (1 if pos < 0 else 0)
        if closing_side:
            reducers = [t for t in triggered if t[0].side == closing_side]
            has_stop = any(t[0].kind in ("stop", "trailing") for t in reducers)
            has_limit = any(t[0].kind not in ("stop", "trailing") for t in reducers)
            if len(reducers) > 1 and has_stop and has_limit:
                self.intrabar_both_hit += 1
                remaining = abs(pos)
                for o, _fp in reducers:          # adverse-first (triggered is already sorted)
                    take = min(o.size, remaining)
                    o.size = take                # order is consumed this bar -> safe to mutate
                    remaining -= take
        return triggered

    def _apply_fill(self, side_sign: int, size: float, price: float, ts: int, is_maker: bool = False) -> None:
        price = adverse_fill_price(price, side_sign, self.slippage)  # adverse: buys up, sells down
        rate = self.maker_fee if is_maker else self.taker_fee
        fee = _fee(size, price, rate, self.multiplier)
        self.cash -= fee
        if self._on_fill is not None:
            self._on_fill(side_sign, size, price, fee, ts, is_maker)
        delta = side_sign * size
        self.cash -= delta * price * self.multiplier   # signed notional moves cash in every case
        pos = self.position
        out = compute_fill(pos.size, pos.avg_price, side_sign, size, price, self.multiplier)
        if out.kind == "open":
            pos.size = out.new_size
            pos.avg_price = out.new_avg_px
            self._entry_fee = fee
            self._entry_ts = ts
            return
        if out.kind == "add":
            pos.size = out.new_size
            pos.avg_price = out.new_avg_px
            self._entry_fee += fee
            return
        # reduce / close / flip: record the closed portion, then update the position
        entry_fee_portion = self._entry_fee * out.portion
        exit_fee_portion = fee * (out.closing_qty / size)   # closing / abs(delta); abs(delta) == size
        self.trades.append(
            Trade(
                entry_price=out.entry_avg_px,
                exit_price=price,
                size=out.closing_qty,
                pnl=out.realized_pnl,
                fees=entry_fee_portion + exit_fee_portion,
                entry_ts=self._entry_ts,
                exit_ts=ts,
            )
        )
        pos.size = out.new_size
        pos.avg_price = out.new_avg_px
        if out.kind == "reduce":
            self._entry_fee -= entry_fee_portion
        elif out.kind == "flip":
            self._entry_fee = fee * (out.leftover / size)
            self._entry_ts = ts
        else:  # close -> flat
            self._entry_fee = 0.0
            self._entry_ts = 0

    def _check_liquidation(self, bar: Bar) -> None:
        """Force-close the position at the bar's adverse extreme if equity there is below maint margin."""
        pos = self.position
        if self.maint_margin <= 0.0 or pos.size == 0:
            return
        adverse = bar.low if pos.size > 0 else bar.high
        eq_ex = self.cash + pos.size * adverse * self.multiplier
        notional_ex = abs(pos.size) * adverse * self.multiplier
        if eq_ex <= self.maint_margin * notional_ex:
            side = -1 if pos.size > 0 else 1
            self._apply_fill(side, abs(pos.size), adverse, bar.ts, is_maker=False)
