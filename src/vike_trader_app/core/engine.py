"""The backtest engine: a bar-at-a-time event loop with next-open fills.

Look-ahead guard: orders submitted during bar *i* fill at the **open of bar i+1**,
because pending orders are filled at the start of each bar *before* the strategy runs.
"""

from dataclasses import dataclass

from .model import Bar, Position, Trade
from .timeframe import parse_timeframe, resample


@dataclass
class Result:
    """Outcome of a backtest run."""

    trades: list[Trade]
    equity_curve: list[float]
    final_equity: float


@dataclass
class _Order:
    """A pending order. ``kind`` in {market, limit, stop, trailing}."""

    kind: str
    side: int            # +1 buy / -1 sell
    size: float
    price: float | None = None    # limit/stop trigger
    trail: float | None = None    # trailing distance (absolute)
    extreme: float | None = None  # running best price since submission (trailing only)


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
        self.cash = cash
        self.position = Position()
        self.trades: list[Trade] = []
        self._pending: list[_Order] = []
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
        strategy._engine = self

    # --- order intake (called from the strategy) ---
    def submit(self, side_sign: int, size: float) -> None:
        size = self._cap_to_leverage(side_sign, size)
        if size > 0.0:
            self._pending.append(_Order("market", side_sign, size))

    def _cap_to_leverage(self, side_sign: int, size: float) -> float:
        """Shrink a market order so the resulting position notional <= leverage * equity."""
        if self.leverage is None:
            return size
        eq = self.equity_now()
        if eq <= 0.0:
            return 0.0
        max_notional = self.leverage * eq
        resulting = abs(self.position.size + side_sign * size)
        if resulting * self._price * self.multiplier <= max_notional:
            return size
        max_pos = max_notional / (self._price * self.multiplier)
        room = max_pos - abs(self.position.size)   # additional shares allowed up to the cap
        return room if room > 0.0 else 0.0

    def submit_limit(self, side_sign: int, size: float, price: float) -> None:
        self._pending.append(_Order("limit", side_sign, size, price=price))

    def submit_stop(self, side_sign: int, size: float, price: float) -> None:
        self._pending.append(_Order("stop", side_sign, size, price=price))

    def submit_trailing(self, side_sign: int, size: float, trail: float) -> None:
        self._pending.append(_Order("trailing", side_sign, size, trail=trail, extreme=self._price))

    def cancel_all(self) -> None:
        self._pending = []

    def submit_close(self) -> None:
        if self.position.size != 0:
            side = -1 if self.position.size > 0 else 1
            self._pending.append(_Order("market", side, abs(self.position.size)))

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
        return [b for b in coarse if b.ts < window_start]

    def forming_for(self, tf: str):
        """The still-building coarse bar for ``tf`` from base bars seen so far, or None."""
        ms, _ = self._tf[tf]
        window_start = self._now - self._now % ms
        window = [b for b in self.bars if window_start <= b.ts <= self._now]
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
        return Result(self.trades, equity_curve, self.equity_now())

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
        """
        self._fill_pending(bar)  # fills before decisions => next-open semantics
        self.strategy.index = i
        self._now = bar.ts
        self._price = bar.close
        if bar.funding is not None and self.position.size != 0:
            self.cash -= self.position.size * bar.close * bar.funding * self.multiplier  # longs pay +funding
        if self._cashflows is not None:
            self.cash += self._cashflows[i]
        self._check_liquidation(bar)
        self._peak = max(self._peak, self.equity_now())
        self.strategy.on_bar(bar)
        return self.equity_now()

    def _fill_pending(self, bar: Bar) -> None:
        still: list[_Order] = []
        for o in self._pending:
            fill_price = self._order_fill_price(o, bar)
            if fill_price is None:
                still.append(o)  # rest until triggered
            else:
                self._apply_fill(o.side, o.size, fill_price, bar.ts, is_maker=o.kind == "limit")
        self._pending = still

    def _order_fill_price(self, o: _Order, bar: Bar):
        """Fill price for an order against ``bar``, or None if it doesn't trigger.

        Trailing stops check the prior extreme's trigger first, then ratchet the
        extreme with this bar — so a bar making a new high can't stop out on its own low.
        """
        if o.kind == "market":
            return bar.open
        if o.kind == "limit":  # buy on a dip to price; sell on a rally to price
            if o.side > 0:
                return o.price if bar.low <= o.price else None
            return o.price if bar.high >= o.price else None
        if o.kind == "stop":  # buy on breakout up; sell on breakdown
            if o.side > 0:
                return o.price if bar.high >= o.price else None
            return o.price if bar.low <= o.price else None
        # trailing: side<0 protects a long (sell-stop trailing the high);
        #           side>0 protects a short (buy-stop trailing the low).
        if o.side < 0:
            trigger = o.extreme - o.trail
            if bar.low <= trigger:
                return trigger
            o.extreme = max(o.extreme, bar.high)
            return None
        trigger = o.extreme + o.trail
        if bar.high >= trigger:
            return trigger
        o.extreme = min(o.extreme, bar.low)
        return None

    def _apply_fill(self, side_sign: int, size: float, price: float, ts: int, is_maker: bool = False) -> None:
        price = price * (1 + side_sign * self.slippage)  # adverse fill: buys up, sells down
        fee = size * price * (self.maker_fee if is_maker else self.taker_fee) * self.multiplier
        self.cash -= fee
        delta = side_sign * size
        pos = self.position
        if pos.size == 0:  # open
            pos.size = delta
            pos.avg_price = price
            self.cash -= delta * price * self.multiplier
            self._entry_fee = fee
            self._entry_ts = ts
        elif (pos.size > 0) == (delta > 0):  # add in the same direction
            new_size = pos.size + delta
            pos.avg_price = (pos.avg_price * abs(pos.size) + price * abs(delta)) / abs(new_size)
            pos.size = new_size
            self.cash -= delta * price * self.multiplier
            self._entry_fee += fee
        else:  # close (full)
            closed = pos.size
            self.cash -= delta * price * self.multiplier
            self.trades.append(
                Trade(
                    entry_price=pos.avg_price,
                    exit_price=price,
                    size=abs(closed),
                    pnl=(price - pos.avg_price) * closed * self.multiplier,
                    fees=self._entry_fee + fee,
                    entry_ts=self._entry_ts,
                    exit_ts=ts,
                )
            )
            pos.size = 0.0
            pos.avg_price = 0.0
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
