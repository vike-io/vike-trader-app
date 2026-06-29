"""The backtest engine: a bar-at-a-time event loop with next-open fills.

Look-ahead guard: orders submitted during bar *i* fill at the **open of bar i+1**,
because pending orders are filled at the start of each bar *before* the strategy runs.
"""

from dataclasses import dataclass

from .bar_buffer import BarSeriesBuffer
from .broker_sim import adverse_fill_price, fee as _fee, funding_charge
from .fill import compute_fill
from .model import Bar, Fill, Position, Trade
from .fill_model import BarFillModel
from .fill_resolution import resolve_intrabar_fills
from .order_intent import backtest_order_request, order_request_to_resting
from .orders import Order
from .sizing import units_from_percent, units_from_value
from .ticks import QuoteTick


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


class SingleSymbolEngine:
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
        catalog=None,
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
        self._on_fill = on_fill   # optional: called per fill (side, size, price, fee, ts, is_maker, order)
        self._on_submit = None    # optional: called per submitted order (order,); default-off
        self._on_cancel = None    # optional: called per cancelled order (order,); default-off
        self._on_funding = None   # optional: called per funding cashflow (amount_signed, ts); default-off
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
        self._catalog_arg = catalog
        self._peak = cash  # peak equity, for drawdown
        # Multi-timeframe buffer — self.bars is the shared list reference.
        self._buf = BarSeriesBuffer(self.bars, timeframes)
        if risk is not None:
            from .order_router import OrderRouter
            strategy._engine = OrderRouter(self, risk)
        else:
            strategy._engine = self

    # --- order intake (called from the strategy) ---
    def _add_pending(self, order: "Order") -> None:
        """Append an order to the pending book and fire on_order_submitted + on_event."""
        self._pending.append(order)
        self.strategy.on_order_submitted(order)
        self.strategy.on_event(order)
        if self._on_submit is not None:
            self._on_submit(order)

    def submit(self, side_sign_or_symbol, side_sign_if_sym=None, size=None,
               weight: float = 0.0, stop=None, raw: bool = False) -> None:
        # Compat: accept both the old single-symbol API  submit(side, size)
        # and the new unified API  submit(symbol, side, size)  — the symbol is ignored.
        if isinstance(side_sign_or_symbol, str):
            # new-API call: submit(symbol, side, size, ...)
            side_sign, size = side_sign_if_sym, size
        else:
            # old-API call: submit(side, size, weight=..., stop=...)
            side_sign = side_sign_or_symbol
            if size is None:
                size = side_sign_if_sym
        del stop, raw
        size = self._cap_to_leverage(side_sign, size)
        if size > 0.0:
            req = backtest_order_request(side=side_sign, qty=size, order_type="market", weight=weight)
            self._add_pending(order_request_to_resting(req))

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

    def submit_limit(self, side_sign_or_symbol, side_or_size=None, size_or_price=None,
                     price=None, weight: float = 0.0, stop=None) -> "Order":
        # Compat: old API submit_limit(side, size, price) / new API submit_limit(sym, side, size, price)
        if isinstance(side_sign_or_symbol, str):
            side_sign, size, price = side_or_size, size_or_price, price
        else:
            side_sign, size, price = side_sign_or_symbol, side_or_size, size_or_price
        del stop
        req = backtest_order_request(side=side_sign, qty=size, order_type="limit", price=price, weight=weight)
        o = order_request_to_resting(req)
        self._add_pending(o)
        return o

    def submit_stop(self, side_sign_or_symbol, side_or_size=None, size_or_price=None,
                    price=None, weight: float = 0.0) -> "Order":
        # Compat: old API submit_stop(side, size, price) / new API submit_stop(sym, side, size, price)
        if isinstance(side_sign_or_symbol, str):
            side_sign, size, price = side_or_size, size_or_price, price
        else:
            side_sign, size, price = side_sign_or_symbol, side_or_size, size_or_price
        req = backtest_order_request(side=side_sign, qty=size, order_type="stop", trigger_price=price, weight=weight)
        o = order_request_to_resting(req)
        self._add_pending(o)
        return o

    def submit_trailing(self, side_sign: int, size: float, trail: float, weight: float = 0.0) -> None:
        extreme_snap = self._price
        req = backtest_order_request(side=side_sign, qty=size, trail=trail, extreme=extreme_snap, weight=weight)
        self._add_pending(order_request_to_resting(req))

    def submit_market_close(self, side_sign: int, size: float) -> None:
        req = backtest_order_request(side=side_sign, qty=size, order_type="market", on_close=True)
        self._add_pending(order_request_to_resting(req))

    def submit_limit_close(self, side_sign: int, size: float, price: float) -> None:
        req = backtest_order_request(side=side_sign, qty=size, order_type="limit", price=price, on_close=True)
        self._add_pending(order_request_to_resting(req))

    def cancel_order(self, symbol, order) -> None:  # noqa: ARG002 - symbol ignored (single-symbol)
        """Remove a specific resting order; no-op if already gone (filled or cancelled)."""
        try:
            self._pending.remove(order)
        except ValueError:
            return
        if self._on_cancel is not None:
            self._on_cancel(order)

    def cancel_all(self, symbol: str | None = None) -> None:  # symbol ignored in single-symbol engine
        removed = self._pending
        self._pending = []
        if self._on_cancel is not None:
            for order in removed:
                self._on_cancel(order)

    def submit_close(self, symbol: str | None = None) -> None:  # symbol ignored in single-symbol engine
        if self.position.size != 0:
            side = -1 if self.position.size > 0 else 1
            req = backtest_order_request(side=side, qty=abs(self.position.size), order_type="market")
            self._add_pending(order_request_to_resting(req))

    def order_target(self, target_size: float) -> None:
        """Market order to move the position to ``target_size`` signed shares."""
        delta = target_size - self.position.size
        if delta > 0:
            self.submit(+1, delta)
        elif delta < 0:
            self.submit(-1, -delta)

    def order_target_value(self, value: float) -> None:
        self.order_target(units_from_value(value, self._price, self.multiplier))

    def order_target_percent(self, pct: float) -> None:
        self.order_target(units_from_percent(pct, self.equity_now(), self._price, self.multiplier))

    @property
    def now(self) -> int:
        """Current simulation time (epoch ms) — the ts of the bar/tick being processed."""
        return self._now

    @property
    def catalog(self):
        """Catalog for Strategy.history() reads; lazily defaults to the global parquet cache."""
        if self._catalog_arg is None:
            from ..data.catalog import Catalog
            self._catalog_arg = Catalog()
        return self._catalog_arg

    def equity_now(self) -> float:
        return self.cash + self.position.size * self._price * self.multiplier

    def drawdown_now(self) -> float:
        """Current drawdown from the running equity peak (0.2 == 20% below peak)."""
        eq = self.equity_now()
        return (self._peak - eq) / self._peak if self._peak > 0 else 0.0

    def bars_for(self, symbol_or_tf, tf: str | None = None):
        """Completed higher-TF bars. Accepts both old API bars_for(tf) and new bars_for(sym, tf)."""
        if tf is None:
            tf = symbol_or_tf  # old single-symbol call: bars_for(tf)
        return self._buf.bars_for(tf, self._now)

    def forming_for(self, symbol_or_tf, tf: str | None = None):
        """Forming coarse bar. Accepts both old API forming_for(tf) and new forming_for(sym, tf)."""
        if tf is None:
            tf = symbol_or_tf  # old single-symbol call: forming_for(tf)
        return self._buf.forming_for(tf, self._now)

    # --- unified Strategy API compat — symbol reads (single-symbol: ignore the key) ---

    def position_of(self, symbol: str) -> "Position":  # noqa: ARG002
        """Return the current position (symbol ignored — single-symbol engine)."""
        return self.position

    def price_of(self, symbol: str) -> float:  # noqa: ARG002
        """Return the last known price (symbol ignored — single-symbol engine)."""
        return self._price

    def _pending_of(self, symbol: str) -> list:  # noqa: ARG002
        """Return pending orders (symbol ignored — single-symbol engine)."""
        return list(self._pending)

    @property
    def symbols(self) -> list[str]:
        """Single dummy symbol key (single-symbol engine has no named symbol)."""
        return ["_"]

    # --- run loop ---
    def run(self) -> Result:
        self.strategy.on_start()
        equity_curve = [self.step(bar, i) for i, bar in enumerate(self.bars)]
        self.strategy.on_stop()
        return Result(self.trades, equity_curve, self.equity_now(),
                      intrabar_both_hit=self.intrabar_both_hit)

    def run_ticks(self, ticks) -> Result:
        """Per-tick run: drive the strategy tick-by-tick (Bar Magnifier + per-tick decisions).

        Shares the bar engine's fill/cost/account core via ``_advance``; ``on_bar`` does NOT fire.
        Fills resolve in true tick order, so stop-vs-target ordering is exact (no pessimistic guess).
        """
        from .consolidator import tick_to_bar
        self.strategy.on_start()
        curve = [self.step_tick(tick_to_bar(t), t, i) for i, t in enumerate(ticks)]
        self.strategy.on_stop()
        return Result(self.trades, curve, self.equity_now(),
                      intrabar_both_hit=self.intrabar_both_hit)

    def step_tick(self, event: Bar, tick, i: int) -> float:
        """Advance by one tick; fill pending BEFORE the handler (no look-ahead), then fire the
        per-tick handler by tick type. Returns equity after the tick."""
        self.strategy.index = i
        self._advance(event)   # no cashflows in tick mode
        if i >= self.strategy.WARMUP:
            if isinstance(tick, QuoteTick):
                self.strategy.on_quote_tick(tick)
            else:
                self.strategy.on_trade_tick(tick)
        return self.equity_now()

    def add_live_bar(self, bar: Bar) -> None:
        """Append a live bar to history and refresh higher-TF aggregates (forward mode).

        Call this *before* ``step`` so the strategy's ``bars(tf)``/``forming(tf)`` see the
        same data a backtest would at this bar. Re-resampling each live bar is O(n) but the
        forward cadence is one bar per interval, so it's negligible.
        """
        self._buf.add_live_bar(bar)

    def step(self, bar: Bar, i: int) -> float:
        """Advance the engine by exactly one bar; return equity after it.

        Pending orders fill at this bar's open *before* the strategy runs (next-open).
        The strategy is gated until ``i >= strategy.WARMUP``. Shares ``_advance`` with the
        per-tick loop (``step_tick``); only the handler that fires differs.
        """
        self.strategy.index = i
        cashflow = self._cashflows[i] if self._cashflows is not None else 0.0
        self._advance(bar, cashflow)
        if i >= self.strategy.WARMUP:  # warm-up gate: skip until indicators have history
            self.strategy.on_bar(bar)
            sched = getattr(self.strategy, "schedule", None)
            if sched is not None:
                for _cb in sched.check_due(self._now, i):
                    _cb()
        return self.equity_now()

    def _advance(self, event, cashflow: float = 0.0) -> None:
        """Shared per-event core (bar OR tick): fill pending, mark price, funding, cashflow,
        liquidation, peak. The caller fires the strategy handler. ``event`` must expose
        ``ts``/``open``/``high``/``low``/``close``/``funding``/``bid``/``ask`` (a ``Bar``)."""
        self._fill_pending(event)  # fills before decisions => next-open / next-tick semantics
        self._now = event.ts
        self._price = event.close
        if event.funding is not None and self.position.size != 0:
            _fc = funding_charge(self.position.size, event.close, event.funding, self.multiplier)
            self.cash -= _fc
            if self._on_funding is not None and _fc != 0.0:
                self._on_funding(-_fc, event.ts)
        self.cash += cashflow
        self._check_liquidation(event)
        self._peak = max(self._peak, self.equity_now())

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
            self._apply_fill(o.side, o.size, fill_price, bar.ts, is_maker=o.kind == "limit", order=o)

    def _resolve_intrabar(self, triggered: "list[tuple[Order, float]]") -> "list[tuple[Order, float]]":
        """Several resting orders triggered in ONE bar — delegates to the shared resolution component.

        See :func:`~vike_trader_app.core.fill_resolution.resolve_intrabar_fills` for the full
        docstring (adverse-first ordering + SL/TP bracket cap)."""
        resolved, both_hit = resolve_intrabar_fills(triggered, self.position.size)
        self.intrabar_both_hit += both_hit
        return resolved

    def _emit_fill_events(self, fill: Fill, kind: str) -> Fill:
        """Fire on_order_filled + on_position_* + on_event for one applied fill (position already updated)."""
        s = self.strategy
        s.on_order_filled(fill)
        s.on_event(fill)
        if kind == "open":
            pos = Position(self.position.size, self.position.avg_price)
            s.on_position_opened(pos); s.on_event(pos)
        elif kind in ("add", "reduce"):
            pos = Position(self.position.size, self.position.avg_price)
            s.on_position_changed(pos); s.on_event(pos)
        elif kind == "close":
            pos = Position(0.0, 0.0)
            s.on_position_closed(pos); s.on_event(pos)
        elif kind == "flip":
            closed = Position(0.0, 0.0)
            s.on_position_closed(closed); s.on_event(closed)
            opened = Position(self.position.size, self.position.avg_price)
            s.on_position_opened(opened); s.on_event(opened)
        return fill

    def _apply_fill(self, side_sign: int, size: float, price: float, ts: int,
                    is_maker: bool = False, order=None) -> Fill:
        price = adverse_fill_price(price, side_sign, self.slippage)  # adverse: buys up, sells down
        rate = self.maker_fee if is_maker else self.taker_fee
        fee = _fee(size, price, rate, self.multiplier)
        self.cash -= fee
        if self._on_fill is not None:
            self._on_fill(side_sign, size, price, fee, ts, is_maker, order)
        fill = Fill(side_sign, size, price, fee, ts, is_maker)
        delta = side_sign * size
        self.cash -= delta * price * self.multiplier   # signed notional moves cash in every case
        pos = self.position
        out = compute_fill(pos.size, pos.avg_price, side_sign, size, price, self.multiplier)
        if out.kind == "open":
            pos.size = out.new_size
            pos.avg_price = out.new_avg_px
            self._entry_fee = fee
            self._entry_ts = ts
            return self._emit_fill_events(fill, "open")
        if out.kind == "add":
            pos.size = out.new_size
            pos.avg_price = out.new_avg_px
            self._entry_fee += fee
            return self._emit_fill_events(fill, "add")
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
        return self._emit_fill_events(fill, out.kind)

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
            fill = self._apply_fill(side, abs(pos.size), adverse, bar.ts, is_maker=False)
            self.strategy.on_liquidation(fill)
            self.strategy.on_event(fill)
