"""Multi-asset / portfolio backtesting on one shared cash account.

Additive sibling to the single-symbol engine. ``PortfolioEngine`` takes aligned
per-symbol bar series and steps timestamp-by-timestamp; orders submitted during a
step fill at each symbol's NEXT bar open (no look-ahead), mirroring the single-symbol
engine. Equity = cash + sum(position size * last close).
"""

from dataclasses import dataclass, field

from .broker_sim import adverse_fill_price, fee as _fee, funding_charge
from .model import Bar, Position, Trade
from .orders import Order, order_fill_price
from .sizing import PassThroughSizer, SizeContext


def _default_sizer():
    """The pass-through sizer (today's behavior): the strategy's literal size, unchanged."""
    return PassThroughSizer()


@dataclass
class PortfolioResult:
    """Outcome of a portfolio backtest run."""

    trades: list[Trade]
    equity_curve: list[float]
    final_equity: float
    per_symbol_pnl: dict = field(default_factory=dict)  # realized + unrealized PnL per symbol
    per_symbol_curves: dict = field(default_factory=dict)  # cumulative PnL curve per symbol (one entry per bar)
    equity_ts: list = field(default_factory=list)  # epoch-ms timestamp for each equity_curve point


class PortfolioStrategy:
    """Base multi-symbol strategy. Override ``on_bar(ts, bars)``.

    ``bars`` is ``{symbol: Bar}`` for the current step. Place orders with
    ``buy/sell/close(symbol, ...)`` or target weights with
    ``order_target_percent(symbol, pct)`` / ``rebalance({symbol: weight})``.
    Read ``position(symbol)``, ``price(symbol)``, ``equity``, ``index``. Partial
    reductions scale the position out (realizing part of the PnL) rather than
    closing it whole.
    """

    def __init__(self) -> None:
        self._engine = None  # set by the engine in run()
        self.index = 0

    @property
    def equity(self) -> float:
        return self._engine.equity_now()

    def position(self, symbol: str) -> Position:
        return self._engine.position_of(symbol)

    def price(self, symbol: str) -> float:
        return self._engine.price_of(symbol)

    def buy(self, symbol: str, size: float, weight: float = 0.0) -> None:
        self._engine.submit(symbol, +1, size, weight=weight)

    def sell(self, symbol: str, size: float, weight: float = 0.0) -> None:
        self._engine.submit(symbol, -1, size, weight=weight)

    def close(self, symbol: str) -> None:
        self._engine.submit_close(symbol)

    def order_target_percent(self, symbol: str, pct: float) -> None:
        """Submit an order to bring ``symbol`` to ``pct`` of current equity."""
        price = self._engine.price_of(symbol)
        if price <= 0:
            return
        target_size = pct * self._engine.equity_now() / price
        delta = target_size - self._engine.position_of(symbol).size
        if delta > 0:
            self.buy(symbol, delta)
        elif delta < 0:
            self.sell(symbol, -delta)

    def rebalance(self, weights: dict) -> None:
        """Target each ``{symbol: weight}`` as a fraction of current equity."""
        for symbol, pct in weights.items():
            self.order_target_percent(symbol, pct)

    def on_bar(self, ts: int, bars: dict) -> None:  # noqa: ARG002 - overridden by users
        """Called once per timestamp, after pending orders for this step have filled."""


class CrossSectionalStrategy(PortfolioStrategy):
    """Top-k rotation: rank the whole universe each rebalance, hold the best ``k``.

    Override ``score(symbol, history)`` (history = the symbol's close list so far,
    point-in-time). The base loop, every ``rebalance_every`` bars, scores every symbol,
    selects the top ``k``, weights them (override ``weights`` — default equal), and
    rebalances — exiting any held symbol that dropped out of the top-k. This is the
    qlib/zipline/bt momentum-rotation / factor-investing pattern.
    """

    k = 1
    rebalance_every = 1
    rebalance_on: str | None = None

    def __init__(self) -> None:
        super().__init__()
        self._hist: dict[str, list[float]] = {}
        self._last_period: str | None = None

    def score(self, symbol: str, history: list[float]):
        """Return a comparable score for ``symbol`` (higher = better), or None to skip."""
        raise NotImplementedError

    def weights(self, winners: list[str]) -> dict:
        """Weights for the selected symbols (default: equal weight)."""
        w = 1.0 / len(winners) if winners else 0.0
        return {s: w for s in winners}

    def on_bar(self, ts: int, bars: dict) -> None:
        # Accumulate price history every bar regardless of rebalance gate.
        for sym, bar in bars.items():
            self._hist.setdefault(sym, []).append(bar.close)

        # Rebalance gate: calendar-period mode or bar-count mode.
        if self.rebalance_on is not None:
            from ..analysis.periods import period_key
            key = period_key(ts, self.rebalance_on)
            if key == self._last_period:
                return  # same calendar period — accumulate history but skip scoring/rebalancing
            self._last_period = key
        elif self.index % self.rebalance_every != 0:
            return

        scores = {}
        for sym in bars:
            sc = self.score(sym, self._hist[sym])
            if sc is not None:
                scores[sym] = sc
        if len(scores) < self.k:
            return
        winners = [s for s, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[: self.k]]
        target = dict(self.weights(winners))
        for sym in self._engine.symbols:  # exit anything held that fell out of the top-k
            if self._engine.position_of(sym).size != 0 and sym not in target:
                target[sym] = 0.0
        self.rebalance(target)


class PortfolioEngine:
    """Runs a `PortfolioStrategy` over aligned per-symbol bar series."""

    def __init__(self, bars_by_symbol, strategy, fee_rate: float = 0.0, cash: float = 10_000.0,
                 slippage: float = 0.0, maker_fee: float | None = None,
                 taker_fee: float | None = None, multiplier: float = 1.0,
                 leverage: float | None = None, maint_margin: float = 0.0,
                 cash_gate: bool = False, active_mask: dict[str, list[bool]] | None = None,
                 timeframes: list[str] | None = None, max_open_positions: int = 0,
                 max_open_long: int = 0, max_open_short: int = 0,
                 sizer=None, volume_limit: float | None = None):
        self.symbols = list(bars_by_symbol)
        self.bars = bars_by_symbol
        lengths = {len(v) for v in bars_by_symbol.values()}
        if len(lengths) > 1:
            raise ValueError("all symbol series must have the same length (aligned)")
        self.n = lengths.pop() if lengths else 0
        self.strategy = strategy
        self.fee_rate = fee_rate
        self.cash = cash
        self.slippage = slippage
        self.maker_fee = maker_fee if maker_fee is not None else fee_rate
        self.taker_fee = taker_fee if taker_fee is not None else fee_rate
        self.multiplier = multiplier
        self.leverage = leverage
        self.maint_margin = maint_margin
        self.cash_gate = cash_gate
        # Optional per-symbol membership windows (dynamic / survivorship-free DataSets): for each
        # symbol a list[bool] aligned to the bar series — True == active member that bar. None
        # (default) == every symbol always active, i.e. today's behavior byte-for-byte.
        self.active_mask = active_mask
        self.max_open_positions = max_open_positions
        # Per-direction open-position caps (0 = no limit).
        self.max_open_long = max_open_long
        self.max_open_short = max_open_short
        # Swappable position sizer (WL PosSizer model). None -> PassThrough (the strategy's literal
        # size), so default behavior is byte-for-byte unchanged.
        self.sizer = sizer or _default_sizer()
        # %-of-volume liquidity cap: fill size clamped to volume_limit * bar.volume. None/0 = no cap.
        self.volume_limit = volume_limit if volume_limit else None
        # Equity peak for drawdown tracking (initialised to starting cash; updated each bar in run()).
        self._equity_peak: float = cash
        self._step = 0  # index of the bar currently being processed in run()
        self.dropped: list = []  # (symbol, kind, size, weight) for gate-dropped fills (diagnostics)
        self.trades: list[Trade] = []
        self._realized: dict[str, float] = {s: 0.0 for s in self.symbols}  # realized PnL per symbol
        self._pos: dict[str, Position] = {s: Position() for s in self.symbols}
        self._pending: dict[str, list[Order]] = {s: [] for s in self.symbols}
        self._entry_fee: dict[str, float] = {s: 0.0 for s in self.symbols}
        self._entry_ts: dict[str, int] = {s: 0 for s in self.symbols}
        self._price: dict[str, float] = {
            s: bars_by_symbol[s][0].open if self.n else 0.0 for s in self.symbols
        }
        # Per-symbol running price extremes since the current position opened (for MAE/MFE).
        # Reset to the fill price when a position opens from flat; updated each bar.
        self._hi_since: dict[str, float] = {s: 0.0 for s in self.symbols}
        self._lo_since: dict[str, float] = {s: float("inf") for s in self.symbols}
        # Per-symbol higher-TF aggregates: symbol -> tf -> (target_ms, [coarse bars]).
        # Empty when no timeframes configured (opt-in — no change to existing behaviour).
        from .timeframe import parse_timeframe, resample
        self._tf: dict[str, dict[str, tuple[int, list]]] = {}
        for s in self.symbols:
            self._tf[s] = {}
            for tf in timeframes or []:
                ms = parse_timeframe(tf)
                self._tf[s][tf] = (ms, resample(self.bars[s], ms))
        # Current step timestamp (shared across all symbols — they share an aligned timeline).
        self._now = self.bars[self.symbols[0]][0].ts if (self.symbols and self.n) else 0
        strategy._engine = self

    # --- membership (dynamic DataSet) ---
    def is_active(self, symbol: str) -> bool:
        """Whether ``symbol`` is an active member on the current step's bar. No mask == always active."""
        return self.active_mask is None or self.active_mask[symbol][self._step]

    def _at_open_cap(self) -> bool:
        """Return True when the MaxOpenPositions cap is set and already reached (count is live)."""
        cap = self.max_open_positions
        return bool(cap) and sum(1 for s in self.symbols if self._pos[s].size != 0) >= cap

    def _long_count(self) -> int:
        """Count of symbols with a long (positive) open position."""
        return sum(1 for s in self.symbols if self._pos[s].size > 0)

    def _short_count(self) -> int:
        """Count of symbols with a short (negative) open position."""
        return sum(1 for s in self.symbols if self._pos[s].size < 0)

    def _at_long_cap(self) -> bool:
        """Return True when the max_open_long cap is set and already reached."""
        cap = self.max_open_long
        return bool(cap) and self._long_count() >= cap

    def _at_short_cap(self) -> bool:
        """Return True when the max_open_short cap is set and already reached."""
        cap = self.max_open_short
        return bool(cap) and self._short_count() >= cap

    # --- ATR helper ---
    def _atr(self, symbol: str, n: int = 14) -> float:
        """Mean true range over the last ``n`` bars up to (and including) ``self._step``.

        True range = max(high-low, |high-prevClose|, |low-prevClose|). Returns 0.0 when there are
        fewer than 2 bars available (need at least one prev-close), using however many bars we have
        if between 2 and n.
        """
        bars = self.bars[symbol]
        end = self._step + 1           # exclusive: bars[:end] are the bars seen so far
        start = max(1, end - n)        # need prev-close -> start at index >= 1
        if end < 2:                    # no prev-close available at all
            return 0.0
        trs = []
        for i in range(start, end):
            bar = bars[i]
            prev_close = bars[i - 1].close
            tr = max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))
            trs.append(tr)
        return sum(trs) / len(trs) if trs else 0.0

    # --- reads exposed to the strategy ---
    def position_of(self, symbol: str) -> Position:
        return self._pos[symbol]

    def price_of(self, symbol: str) -> float:
        return self._price[symbol]

    def equity_now(self) -> float:
        return self.cash + sum(self._pos[s].size * self._price[s] * self.multiplier for s in self.symbols)

    # --- sizing (WL PosSizer) ---
    def _is_opening(self, symbol: str, side_sign: int) -> bool:
        """True when an order on ``symbol`` in direction ``side_sign`` opens-from-flat or adds in the
        same direction (an entry). False when it reduces/closes/flips. Mirrors the fill-loop's
        ``opening = pos.size == 0 or (pos.size > 0) == (side > 0)`` test."""
        pos = self._pos[symbol]
        return pos.size == 0 or (pos.size > 0) == (side_sign > 0)

    def _size_entry(self, symbol: str, side_sign: int, size: float, raw: bool) -> float:
        """Run the sizer on opening/increasing entries; pass ``size`` through untouched when ``raw``
        (explicit order_target_* sizing) or when the order reduces/closes the position."""
        if raw or not self._is_opening(symbol, side_sign):
            return size
        equity = self.equity_now()
        peak = self._equity_peak
        drawdown = max(0.0, 1.0 - equity / peak) if peak > 0 else 0.0
        return self.sizer.size(SizeContext(
            symbol, side_sign, size, self._price[symbol],
            equity, self.cash, self.multiplier,
            atr=self._atr(symbol),
            drawdown=drawdown,
        ))

    # --- leverage / risk helpers ---
    def _cap_to_leverage(self, symbol: str, side_sign: int, size: float) -> float:
        """Shrink an opening/adding market order so projected TOTAL account notional <= leverage*equity.
        Account-level: sums notional across all symbols (+ pending market orders). Reducing/closing
        orders (opposite to the current position) are never shrunk."""
        if self.leverage is None:
            return size
        eq = self.equity_now()
        if eq <= 0.0:
            return 0.0
        pos = self._pos[symbol]
        # only cap orders that increase exposure on this symbol (open-from-flat or same-direction add)
        if pos.size != 0 and (pos.size > 0) != (side_sign > 0):
            return size                                  # reducing/closing: never capped
        max_notional = self.leverage * eq
        # current total notional across all symbols + this symbol's already-pending market opens
        cur = sum(abs(self._pos[s].size) * self._price[s] * self.multiplier for s in self.symbols)
        pending = sum(o.side * o.size for o in self._pending[symbol] if o.kind == "market") * \
            self._price[symbol] * self.multiplier
        room_notional = max_notional - cur - abs(pending)
        if room_notional <= 0.0:
            return 0.0
        room = room_notional / (self._price[symbol] * self.multiplier)
        return size if size <= room else room

    def _check_liquidation(self, cur: dict) -> None:
        """Account-level margin call: if total equity at every position's adverse extreme is at or
        below maint_margin * total adverse notional, force-close ALL positions at their adverse marks."""
        if self.maint_margin <= 0.0:
            return
        held = [s for s in self.symbols if self._pos[s].size != 0]
        if not held:
            return
        eq_adv = self.cash
        notional_adv = 0.0
        for s in held:
            pos = self._pos[s]
            adverse = cur[s].low if pos.size > 0 else cur[s].high
            eq_adv += pos.size * adverse * self.multiplier
            notional_adv += abs(pos.size) * adverse * self.multiplier
        if eq_adv <= self.maint_margin * notional_adv:
            for s in held:
                pos = self._pos[s]
                adverse = cur[s].low if pos.size > 0 else cur[s].high
                side = -1 if pos.size > 0 else 1
                self._apply_fill(s, side, abs(pos.size), adverse, cur[s].ts)

    def _close_inactive(self, cur):
        """Force-close any held position whose symbol is inactive this bar (WL removal-day exit),
        at the bar's OPEN (next-open from the last active bar — no look-ahead)."""
        if self.active_mask is None:
            return
        for s in self.symbols:
            if self._pos[s].size != 0 and not self.is_active(s):
                side = -1 if self._pos[s].size > 0 else 1
                self._apply_fill(s, side, abs(self._pos[s].size), cur[s].open, cur[s].ts)

    # --- order intake ---
    def submit(self, symbol: str, side_sign: int, size: float, weight: float = 0.0,
               raw: bool = False) -> None:
        size = self._size_entry(symbol, side_sign, size, raw)  # sizer first, then leverage cap
        size = self._cap_to_leverage(symbol, side_sign, size)
        if size > 0.0:
            self._pending[symbol].append(Order("market", side_sign, size, weight=weight))

    def submit_close(self, symbol: str) -> None:
        pos = self._pos[symbol]
        if pos.size != 0:
            side = -1 if pos.size > 0 else 1
            self._pending[symbol].append(Order("market", side, abs(pos.size)))

    def submit_limit(self, symbol: str, side_sign: int, size: float, price: float, weight: float = 0.0,
                     raw: bool = False) -> None:
        size = self._size_entry(symbol, side_sign, size, raw)
        self._pending[symbol].append(Order("limit", side_sign, size, price=price, weight=weight))

    def submit_stop(self, symbol: str, side_sign: int, size: float, price: float, weight: float = 0.0,
                    raw: bool = False) -> None:
        size = self._size_entry(symbol, side_sign, size, raw)
        self._pending[symbol].append(Order("stop", side_sign, size, price=price, weight=weight))

    def submit_trailing(self, symbol: str, side_sign: int, size: float, trail: float, weight: float = 0.0,
                        raw: bool = False) -> None:
        size = self._size_entry(symbol, side_sign, size, raw)
        self._pending[symbol].append(Order("trailing", side_sign, size, trail=trail,
                                           extreme=self._price[symbol], weight=weight))

    def submit_market_close(self, symbol: str, side_sign: int, size: float, weight: float = 0.0,
                            raw: bool = False) -> None:
        size = self._size_entry(symbol, side_sign, size, raw)
        size = self._cap_to_leverage(symbol, side_sign, size)
        if size > 0.0:
            self._pending[symbol].append(Order("market_close", side_sign, size, weight=weight))

    def submit_limit_close(self, symbol: str, side_sign: int, size: float, price: float, weight: float = 0.0,
                           raw: bool = False) -> None:
        size = self._size_entry(symbol, side_sign, size, raw)
        self._pending[symbol].append(Order("limit_close", side_sign, size, price=price, weight=weight))

    def cancel_all(self, symbol: str) -> None:
        self._pending[symbol] = []

    # --- higher-TF reads (mirror BacktestEngine, per symbol) ---
    def bars_for(self, symbol: str, tf: str):
        """Completed higher-TF bars for ``symbol`` visible at the current step (no look-ahead)."""
        ms, coarse = self._tf[symbol][tf]
        window_start = self._now - self._now % ms
        return [b for b in coarse if b.ts < window_start]

    def forming_for(self, symbol: str, tf: str):
        """The still-building coarse bar for ``tf`` / ``symbol`` from base bars seen so far, or None."""
        ms, _ = self._tf[symbol][tf]
        window_start = self._now - self._now % ms
        window = [b for b in self.bars[symbol] if window_start <= b.ts <= self._now]
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
    def run(self) -> PortfolioResult:
        equity_curve: list[float] = []
        equity_ts: list[int] = []
        per_symbol_curve: dict[str, list[float]] = {s: [] for s in self.symbols}
        for i in range(self.n):
            self._step = i  # current bar index — read by is_active() during the fill phase
            cur = {s: self.bars[s][i] for s in self.symbols}
            self._now = cur[self.symbols[0]].ts if self.symbols else 0  # shared aligned timestamp
            if self.cash_gate:
                self._fill_step_gated(cur)  # cross-symbol shared-cash priority + drop gate
            else:
                for s in self.symbols:
                    self._fill_pending(s, cur[s])  # fills before decisions => next-open
            self.strategy.index = i
            for s in self.symbols:
                bar = cur[s]
                self._price[s] = bar.close
                if bar.funding is not None and self._pos[s].size != 0:
                    self.cash -= funding_charge(self._pos[s].size, bar.close, bar.funding, self.multiplier)
                # Update MAE/MFE extremes for any open position using this bar's high/low (no look-ahead).
                if self._pos[s].size != 0:
                    self._hi_since[s] = max(self._hi_since[s], bar.high)
                    self._lo_since[s] = min(self._lo_since[s], bar.low)
            self._close_inactive(cur)  # WL removal-day exit: drop any position now out of membership
            self._check_liquidation(cur)
            ts = self.bars[self.symbols[0]][i].ts if self.symbols else 0
            self.strategy.on_bar(ts, cur)
            eq = self.equity_now()
            self._equity_peak = max(self._equity_peak, eq)  # track running peak for drawdown
            equity_curve.append(eq)
            equity_ts.append(ts)
            for s in self.symbols:
                pos = self._pos[s]
                per_symbol_curve[s].append(self._realized[s] + pos.size * (self._price[s] - pos.avg_price) * self.multiplier)
        # attribution: realized PnL + open-position mark-to-market, per symbol
        per_symbol = {
            s: self._realized[s] + self._pos[s].size * (self._price[s] - self._pos[s].avg_price) * self.multiplier
            for s in self.symbols
        }
        return PortfolioResult(self.trades, equity_curve, self.equity_now(), per_symbol_pnl=per_symbol, per_symbol_curves=per_symbol_curve, equity_ts=equity_ts)

    def _fill_pending(self, symbol: str, bar: Bar) -> None:
        still = []
        for o in self._pending[symbol]:
            fp = order_fill_price(o, bar)
            if fp is None:
                still.append(o)               # rest until triggered
            else:
                pos = self._pos[symbol]
                opening = pos.size == 0 or (pos.size > 0) == (o.side > 0)  # open-from-flat or same-dir add
                if opening and not self.is_active(symbol):
                    continue                  # inactive member: drop opening/adding fills (reduces still apply)
                if pos.size == 0 and self._at_open_cap():
                    continue                  # MaxOpenPositions cap reached: drop new-symbol opens
                # Per-direction caps: only block new-symbol opens (pos.size == 0), not adds to existing
                if pos.size == 0 and o.side > 0 and self._at_long_cap():
                    continue                  # max_open_long cap reached
                if pos.size == 0 and o.side < 0 and self._at_short_cap():
                    continue                  # max_open_short cap reached
                # %-of-volume liquidity cap: clamp fill size to volume_limit * bar.volume.
                fill_size = o.size
                if self.volume_limit:
                    allowed = self.volume_limit * bar.volume
                    if fill_size > allowed:
                        dropped_size = fill_size - allowed
                        self.dropped.append((symbol, "volume_cap", dropped_size, 0.0))
                        fill_size = allowed
                if fill_size > 0:
                    self._apply_fill(symbol, o.side, fill_size, fp, bar.ts, is_maker=o.kind == "limit")
        self._pending[symbol] = still

    def _fill_step_gated(self, cur):
        """Collect every triggered fill across symbols this bar, then apply with a shared-cash gate.
        Reductions/closes (which free cash) fill first; opening/adding fills are sorted by weight desc
        (ties: trigger order) and applied only while they stay fundable — the rest are DROPPED (WL's
        'dropped due to insufficient funds'). Trailing extremes ratchet exactly once via order_fill_price.
        """
        opens, frees = [], []
        seq = 0
        for s in self.symbols:
            still = []
            for o in self._pending[s]:
                fp = order_fill_price(o, cur[s])
                if fp is None:
                    still.append(o)
                    continue
                pos = self._pos[s]
                increasing = pos.size == 0 or (pos.size > 0) == (o.side > 0)
                if increasing and not self.is_active(s):
                    continue              # inactive member: drop opening/adding fills before the cash gate
                (opens if increasing else frees).append((s, o, fp, seq))
                seq += 1
            self._pending[s] = still
        # reductions/closes first — they free cash and never get gated
        for s, o, fp, _ in frees:
            fill_size = o.size
            if self.volume_limit:
                allowed = self.volume_limit * cur[s].volume
                if fill_size > allowed:
                    dropped_size = fill_size - allowed
                    self.dropped.append((s, "volume_cap", dropped_size, 0.0))
                    fill_size = allowed
            if fill_size > 0:
                self._apply_fill(s, o.side, fill_size, fp, cur[s].ts, is_maker=o.kind == "limit")
        # opens/adds: highest weight first, ties by trigger order; drop the unfundable
        for s, o, fp, _ in sorted(opens, key=lambda t: (-t[1].weight, t[3])):
            # MaxOpenPositions cap: re-checked live so count updates as positions open during this loop
            if self._pos[s].size == 0 and self._at_open_cap():
                self.dropped.append((s, o.kind, o.size, o.weight))
                continue
            # Per-direction caps: re-checked live; only block new-symbol opens
            if self._pos[s].size == 0 and o.side > 0 and self._at_long_cap():
                self.dropped.append((s, o.kind, o.size, o.weight))
                continue
            if self._pos[s].size == 0 and o.side < 0 and self._at_short_cap():
                self.dropped.append((s, o.kind, o.size, o.weight))
                continue
            # %-of-volume liquidity cap: clamp fill size to volume_limit * bar.volume.
            fill_size = o.size
            if self.volume_limit:
                allowed = self.volume_limit * cur[s].volume
                if fill_size > allowed:
                    dropped_size = fill_size - allowed
                    self.dropped.append((s, "volume_cap", dropped_size, 0.0))
                    fill_size = allowed
            if fill_size <= 0:
                continue
            slipped = adverse_fill_price(fp, o.side, self.slippage)
            rate = self.maker_fee if o.kind == "limit" else self.taker_fee
            fee = _fee(fill_size, slipped, rate, self.multiplier)
            cash_impact = -(o.side * fill_size) * slipped * self.multiplier - fee  # buys cost, sells free
            if self.cash + cash_impact < 0.0:
                self.dropped.append((s, o.kind, fill_size, o.weight))
                continue
            self._apply_fill(s, o.side, fill_size, fp, cur[s].ts, is_maker=o.kind == "limit")

    def _apply_fill(self, symbol: str, side_sign: int, size: float, price: float, ts: int,
                    is_maker: bool = False) -> None:
        price = adverse_fill_price(price, side_sign, self.slippage)
        rate = self.maker_fee if is_maker else self.taker_fee
        fee = _fee(size, price, rate, self.multiplier)
        delta = side_sign * size
        pos = self._pos[symbol]
        self.cash -= fee  # transaction cost
        self.cash -= delta * price * self.multiplier  # signed notional moves cash in every case

        if pos.size == 0:  # open from flat
            pos.size = delta
            pos.avg_price = price
            self._entry_fee[symbol] = fee
            self._entry_ts[symbol] = ts
            # Reset excursion extremes to the fill price so MAE/MFE starts from entry.
            self._hi_since[symbol] = price
            self._lo_since[symbol] = price
            return

        if (pos.size > 0) == (delta > 0):  # add in the same direction
            new_size = pos.size + delta
            pos.avg_price = (pos.avg_price * abs(pos.size) + price * abs(delta)) / abs(new_size)
            pos.size = new_size
            self._entry_fee[symbol] += fee
            return

        # opposite direction: reduce part of the position, fully close, or close-and-flip.
        sign = 1.0 if pos.size > 0 else -1.0
        closing = min(abs(delta), abs(pos.size))  # units of the existing position retired
        portion = closing / abs(pos.size)
        entry_fee_portion = self._entry_fee[symbol] * portion
        exit_fee_portion = fee * (closing / abs(delta)) if delta != 0 else 0.0
        realized = (price - pos.avg_price) * (sign * closing) * self.multiplier  # signed: works for shorts
        self._realized[symbol] += realized
        # Compute MAE/MFE fractions relative to entry price (guard entry==0).
        entry = pos.avg_price
        hi = self._hi_since[symbol]
        lo = self._lo_since[symbol]
        if entry != 0:
            if pos.size > 0:  # long
                mfe = (hi - entry) / entry
                mae = (lo - entry) / entry   # negative: adverse
            else:              # short
                mfe = (entry - lo) / entry
                mae = (entry - hi) / entry   # negative: adverse
        else:
            mfe, mae = 0.0, 0.0
        self.trades.append(
            Trade(
                entry_price=pos.avg_price,
                exit_price=price,
                size=closing,
                pnl=realized,
                fees=entry_fee_portion + exit_fee_portion,
                entry_ts=self._entry_ts[symbol],
                exit_ts=ts,
                symbol=symbol,
                mae=mae,
                mfe=mfe,
            )
        )
        remaining = abs(pos.size) - closing
        if remaining > 1e-12:  # partial reduce: keep the remainder at the same cost basis
            pos.size = sign * remaining
            self._entry_fee[symbol] -= entry_fee_portion
            return

        leftover = abs(delta) - closing  # crossed through zero -> open opposite side
        if leftover > 1e-12:
            pos.size = (1.0 if delta > 0 else -1.0) * leftover
            pos.avg_price = price
            self._entry_fee[symbol] = fee * (leftover / abs(delta))
            self._entry_ts[symbol] = ts
            # Reset excursion extremes for the new opposite-side position.
            self._hi_since[symbol] = price
            self._lo_since[symbol] = price
        else:  # flat
            pos.size = 0.0
            pos.avg_price = 0.0
            self._entry_fee[symbol] = 0.0
            self._entry_ts[symbol] = 0
