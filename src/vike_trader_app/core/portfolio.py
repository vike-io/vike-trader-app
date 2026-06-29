"""Multi-asset / portfolio backtesting on one shared cash account.

Additive sibling to the single-symbol engine. ``PortfolioEngine`` takes aligned
per-symbol bar series and steps timestamp-by-timestamp; orders submitted during a
step fill at each symbol's NEXT bar open (no look-ahead), mirroring the single-symbol
engine. Equity = cash + sum(position size * last close).
"""

import bisect
import logging
import warnings
from dataclasses import dataclass, field, replace
from operator import attrgetter

from .broker_sim import adverse_fill_price, fee as _fee, funding_charge
from .fill import compute_fill
from .instrument_id import format_instrument
from .model import Bar, Fill, Position, Trade
from .orders import Order, order_fill_price
from .sizing import PassThroughSizer, SizeContext
from .strategy import Strategy

logger = logging.getLogger(__name__)

_BAR_TS = attrgetter("ts")   # bisect key for the per-symbol higher-TF reads (mirror BacktestEngine)


def _default_sizer():
    """The pass-through sizer (today's behavior): the strategy's literal size, unchanged."""
    return PassThroughSizer()


@dataclass
class SymbolState:
    """All mutable per-symbol state for the portfolio engine — ONE per symbol (was 11 parallel
    ``dict[str, …]`` keyed by symbol, mutated in lockstep across the engine)."""
    pos: Position = field(default_factory=Position)
    pending: list = field(default_factory=list)
    realized: float = 0.0           # realized PnL
    stop: float | None = None       # active protective stop price (None == none)
    entry_fee: float = 0.0
    entry_ts: int = 0
    price: float = 0.0              # last seen price
    hi_since: float = 0.0          # running high since the position opened (MAE/MFE)
    lo_since: float = float("inf")  # running low since the position opened
    tf: dict = field(default_factory=dict)    # higher-TF aggregates: tf -> (target_ms, [coarse bars])
    sub: list = field(default_factory=list)   # granular sub-bars bucketed per coarse step


@dataclass
class PortfolioResult:
    """Outcome of a portfolio backtest run."""

    trades: list[Trade]
    equity_curve: list[float]
    final_equity: float
    per_symbol_pnl: dict = field(default_factory=dict)  # realized + unrealized PnL per symbol
    per_symbol_curves: dict = field(default_factory=dict)  # cumulative PnL curve per symbol (one entry per bar)
    equity_ts: list = field(default_factory=list)  # epoch-ms timestamp for each equity_curve point
    benchmark_curve: list = field(default_factory=list)  # equal-weight buy-&-hold benchmark equity curve
    benchmark_label: str = ""  # human-readable benchmark description


class PortfolioStrategy(Strategy):
    """Base multi-symbol strategy. Override ``on_bar(ts, bars)``.

    .. deprecated::
        Subclass the unified :class:`~vike_trader_app.core.strategy.Strategy` instead
        and implement ``on_bar(bar)`` (one bar per symbol per call).

    ``bars`` is ``{symbol: Bar}`` for the current step. Place orders with
    ``buy/sell/close(symbol, ...)`` or target weights with
    ``order_target_percent(symbol, pct)`` / ``rebalance({symbol: weight})``.
    Read ``position(symbol)``, ``price(symbol)``, ``equity``, ``index``. Partial
    reductions scale the position out (realizing part of the PnL) rather than
    closing it whole.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        warnings.warn(
            "PortfolioStrategy is deprecated; subclass the unified Strategy "
            "(one on_bar(bar) per symbol).",
            DeprecationWarning,
            stacklevel=2,
        )

    def __init__(self) -> None:
        super().__init__()  # sets _engine, index, schedule via Strategy.__init__

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

    def _on_step(self, ts: int, bars: dict) -> None:
        """Engine-facing per-step hook. Default = the legacy bundle handler."""
        self.on_bar(ts, bars)


class CrossSectionalStrategy(Strategy):
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
        from .schedule import EveryNBars, PeriodStart
        rule = PeriodStart(self.rebalance_on) if self.rebalance_on is not None else EveryNBars(self.rebalance_every)
        self.schedule.on(rule, self._rebalance)

    def score(self, symbol: str, history: list[float]):
        """Return a comparable score for ``symbol`` (higher = better), or None to skip."""
        raise NotImplementedError

    def weights(self, winners: list[str]) -> dict:
        """Weights for the selected symbols (default: equal weight)."""
        w = 1.0 / len(winners) if winners else 0.0
        return {s: w for s in winners}

    def on_bar(self, bar) -> None:
        self._hist.setdefault(self._sym_key(bar.symbol), []).append(bar.close)

    def _rebalance(self) -> None:
        scores = {}
        for sym in self._engine.symbols:
            hist = self._hist.get(sym)
            if not hist:
                continue
            sc = self.score(sym, hist)
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
                 sizer=None, volume_limit: float | None = None,
                 granular_by_symbol: dict[str, list[Bar]] | None = None,
                 default_venue: str | None = None):
        self.symbols = list(bars_by_symbol)
        # Pre-tag each bar with its instrument id (SYMBOL.VENUE) once at construction.
        # With default_venue=None, format_instrument returns the bare symbol so bar.symbol
        # is always populated (non-None) regardless of whether a venue is provided.
        self.bars = {
            s: [replace(b, symbol=format_instrument(default_venue, s)) for b in series]
            for s, series in bars_by_symbol.items()
        }
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
        # All per-symbol mutable state lives in ONE SymbolState per symbol (was 11 parallel dicts).
        # price seeds to the first open; sub is sized to the step count and filled from granular data
        # below; pos/realized/stop/entry_fee/entry_ts/hi_since/lo_since/tf use the dataclass defaults.
        self._sym: dict[str, SymbolState] = {
            s: SymbolState(price=(bars_by_symbol[s][0].open if self.n else 0.0),
                           sub=[[] for _ in range(self.n)])
            for s in self.symbols
        }
        # Per-symbol higher-TF aggregates: symbol -> tf -> (target_ms, [coarse bars]). Empty when no
        # timeframes are configured (opt-in — no change to existing behaviour).
        from .timeframe import parse_timeframe, resample
        for s in self.symbols:
            for tf in timeframes or []:
                ms = parse_timeframe(tf)
                self._sym[s].tf[tf] = (ms, resample(self.bars[s], ms))
        # Current step timestamp (shared across all symbols — they share an aligned timeline).
        self._now = self.bars[self.symbols[0]][0].ts if (self.symbols and self.n) else 0
        # --- granular (intraday sub-bar) fill processing (WL "Use Granular Limit/Stop Processing") ---
        # Opt-in finer bars per symbol. We bucket each symbol's sub-bars into the coarse step they fall
        # into: sub-bar with ts ∈ [coarse[i].ts, coarse[i+1].ts) belongs to step i; the LAST coarse bar
        # absorbs everything with ts >= coarse[i].ts (no upper edge). self._sym[symbol].sub is a list aligned
        # to self.bars[symbol] step indices; each entry is the (time-ordered) sub-bar list for that step.
        # A symbol with no granular data gets all-empty lists -> the coarse path is used for every step.
        self.granular_by_symbol = granular_by_symbol
        for s in self.symbols:                       # self._sym[s].sub already sized to self.n above
            subs = (granular_by_symbol or {}).get(s)
            if not subs:
                continue
            coarse = self.bars[s]
            edges = [b.ts for b in coarse]  # ascending coarse timestamps (aligned series)
            for sub in sorted(subs, key=lambda b: b.ts):
                # Find the coarse step i such that edges[i] <= sub.ts < edges[i+1]
                # (last step has no upper edge). bisect_right gives the count of edges <= sub.ts.
                i = bisect.bisect_right(edges, sub.ts) - 1
                if i < 0:
                    continue  # sub-bar precedes the first coarse bar — not attributable to any step
                self._sym[s].sub[i].append(sub)
        strategy._engine = self

    # --- membership (dynamic DataSet) ---
    def is_active(self, symbol: str) -> bool:
        """Whether ``symbol`` is an active member on the current step's bar. No mask == always active."""
        return self.active_mask is None or self.active_mask[symbol][self._step]

    def _at_open_cap(self) -> bool:
        """Return True when the MaxOpenPositions cap is set and already reached (count is live)."""
        cap = self.max_open_positions
        return bool(cap) and sum(1 for s in self.symbols if self._sym[s].pos.size != 0) >= cap

    def _long_count(self) -> int:
        """Count of symbols with a long (positive) open position."""
        return sum(1 for s in self.symbols if self._sym[s].pos.size > 0)

    def _short_count(self) -> int:
        """Count of symbols with a short (negative) open position."""
        return sum(1 for s in self.symbols if self._sym[s].pos.size < 0)

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
        return self._sym[symbol].pos

    def price_of(self, symbol: str) -> float:
        return self._sym[symbol].price

    @property
    def now(self) -> int:
        return self._now

    def equity_now(self) -> float:
        return self.cash + sum(self._sym[s].pos.size * self._sym[s].price * self.multiplier for s in self.symbols)

    def drawdown_now(self) -> float:
        """Current drawdown from the running equity peak (0..1).

        Uses the same ``_equity_peak`` that the run loop updates each bar.
        Returns 0.0 when the peak has not been established yet.
        """
        eq = self.equity_now()
        peak = self._equity_peak
        if peak <= 0.0:
            return 0.0
        return max(0.0, (peak - eq) / peak)

    # --- sizing (WL PosSizer) ---
    def _is_opening(self, symbol: str, side_sign: int) -> bool:
        """True when an order on ``symbol`` in direction ``side_sign`` opens-from-flat or adds in the
        same direction (an entry). False when it reduces/closes/flips. Mirrors the fill-loop's
        ``opening = pos.size == 0 or (pos.size > 0) == (side > 0)`` test."""
        pos = self._sym[symbol].pos
        return pos.size == 0 or (pos.size > 0) == (side_sign > 0)

    def _open_risk(self) -> float:
        """Total $ risk currently open across the book: for every symbol with an armed protective
        stop and a non-flat position, ``|price - stop| * |size| * multiplier``. Used by PortfolioHeat."""
        return sum(
            abs(self._sym[s].price - self._sym[s].stop) * abs(self._sym[s].pos.size) * self.multiplier
            for s in self.symbols
            if self._sym[s].stop is not None and self._sym[s].pos.size != 0
        )

    def _size_entry(self, symbol: str, side_sign: int, size: float, raw: bool, stop=None) -> float:
        """Run the sizer on opening/increasing entries; pass ``size`` through untouched when ``raw``
        (explicit order_target_* sizing) or when the order reduces/closes the position.

        ``stop`` is the protective stop price declared for this entry; it is fed to the SizeContext
        as ``risk_stop`` so risk-based sizers (MaxRiskPct / PortfolioHeat) can size off it."""
        if raw or not self._is_opening(symbol, side_sign):
            return size
        equity = self.equity_now()
        peak = self._equity_peak
        drawdown = max(0.0, 1.0 - equity / peak) if peak > 0 else 0.0
        return self.sizer.size(SizeContext(
            symbol, side_sign, size, self._sym[symbol].price,
            equity, self.cash, self.multiplier,
            atr=self._atr(symbol),
            drawdown=drawdown,
            risk_stop=stop,
            open_risk=self._open_risk(),
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
        pos = self._sym[symbol].pos
        # only cap orders that increase exposure on this symbol (open-from-flat or same-direction add)
        if pos.size != 0 and (pos.size > 0) != (side_sign > 0):
            return size                                  # reducing/closing: never capped
        max_notional = self.leverage * eq
        # current total notional across all symbols + ALL symbols' already-pending market opens this
        # bar. Summing only THIS symbol's pending let two same-bar opens on different symbols each
        # ignore the other, so the book silently over-leveraged (each abs() per symbol — a long on A
        # and a short on B both consume leverage). The order being capped is appended AFTER this, so
        # it isn't double-counted here.
        cur = sum(abs(self._sym[s].pos.size) * self._sym[s].price * self.multiplier for s in self.symbols)
        pending = sum(
            abs(sum(o.side * o.size for o in self._sym[s].pending if o.kind == "market"))
            * self._sym[s].price * self.multiplier
            for s in self.symbols
        )
        room_notional = max_notional - cur - pending
        if room_notional <= 0.0:
            return 0.0
        room = room_notional / (self._sym[symbol].price * self.multiplier)
        return size if size <= room else room

    def _check_liquidation(self, cur: dict) -> None:
        """Account-level margin call: if total equity at every position's adverse extreme is at or
        below maint_margin * total adverse notional, force-close ALL positions at their adverse marks."""
        if self.maint_margin <= 0.0:
            return
        held = [s for s in self.symbols if self._sym[s].pos.size != 0]
        if not held:
            return
        eq_adv = self.cash
        notional_adv = 0.0
        for s in held:
            pos = self._sym[s].pos
            adverse = cur[s].low if pos.size > 0 else cur[s].high
            eq_adv += pos.size * adverse * self.multiplier
            notional_adv += abs(pos.size) * adverse * self.multiplier
        if eq_adv <= self.maint_margin * notional_adv:
            for s in held:
                pos = self._sym[s].pos
                adverse = cur[s].low if pos.size > 0 else cur[s].high
                side = -1 if pos.size > 0 else 1
                self._apply_fill(s, side, abs(pos.size), adverse, cur[s].ts)

    def _check_protective_stops(self, cur, skip=None):
        """Force-close any position whose armed protective stop is breached by THIS bar.

        Runs at the top of the step's fill phase (before this step's resting/market fills and before
        on_bar), so it only ever sees stops armed on a PRIOR step — an entry can never be stopped on
        its own entry bar (the stop is armed during that bar's fill phase, checked from the next one).
        Long: breach when ``low <= stop`` (close at the stop). Short: breach when ``high >= stop``.
        ``skip`` is the set of symbols handled by the granular path this step (their stop is checked
        inside _fill_pending_granular, so we must not double-apply it here).
        """
        skip = skip or set()
        for s in self.symbols:
            if s in skip:
                continue
            stop = self._sym[s].stop
            pos = self._sym[s].pos
            if stop is None or pos.size == 0:
                continue
            if pos.size > 0:  # long protective sell-stop
                if cur[s].low <= stop:
                    self._apply_fill(s, -1, abs(pos.size), stop, cur[s].ts)
                    self._sym[s].stop = None  # _apply_fill already clears on flat; explicit for clarity
            else:             # short protective buy-stop
                if cur[s].high >= stop:
                    self._apply_fill(s, +1, abs(pos.size), stop, cur[s].ts)
                    self._sym[s].stop = None

    def _close_inactive(self, cur):
        """Force-close any held position whose symbol is inactive this bar (WL removal-day exit),
        at the bar's OPEN (next-open from the last active bar — no look-ahead)."""
        if self.active_mask is None:
            return
        for s in self.symbols:
            if self._sym[s].pos.size != 0 and not self.is_active(s):
                side = -1 if self._sym[s].pos.size > 0 else 1
                self._apply_fill(s, side, abs(self._sym[s].pos.size), cur[s].open, cur[s].ts)

    # --- order intake ---
    def submit(self, symbol: str, side_sign: int, size: float, weight: float = 0.0,
               raw: bool = False, stop=None):
        size = self._size_entry(symbol, side_sign, size, raw, stop=stop)  # sizer first, then leverage cap
        size = self._cap_to_leverage(symbol, side_sign, size)
        if size > 0.0:
            o = Order("market", side_sign, size, weight=weight, stop=stop)
            self._sym[symbol].pending.append(o)
            return o
        return None

    def submit_close(self, symbol: str) -> None:
        pos = self._sym[symbol].pos
        if pos.size != 0:
            side = -1 if pos.size > 0 else 1
            self._sym[symbol].pending.append(Order("market", side, abs(pos.size)))

    def submit_limit(self, symbol: str, side_sign: int, size: float, price: float, weight: float = 0.0,
                     raw: bool = False, stop=None):
        size = self._size_entry(symbol, side_sign, size, raw, stop=stop)
        o = Order("limit", side_sign, size, price=price, weight=weight, stop=stop)
        self._sym[symbol].pending.append(o)
        return o

    def submit_stop(self, symbol: str, side_sign: int, size: float, price: float, weight: float = 0.0,
                    raw: bool = False):
        size = self._size_entry(symbol, side_sign, size, raw)
        o = Order("stop", side_sign, size, price=price, weight=weight)
        self._sym[symbol].pending.append(o)
        return o

    def submit_trailing(self, symbol: str, side_sign: int, size: float, trail: float, weight: float = 0.0,
                        raw: bool = False):
        size = self._size_entry(symbol, side_sign, size, raw)
        o = Order("trailing", side_sign, size, trail=trail,
                  extreme=self._sym[symbol].price, weight=weight)
        self._sym[symbol].pending.append(o)
        return o

    def submit_market_close(self, symbol: str, side_sign: int, size: float, weight: float = 0.0,
                            raw: bool = False):
        size = self._size_entry(symbol, side_sign, size, raw)
        size = self._cap_to_leverage(symbol, side_sign, size)
        if size > 0.0:
            o = Order("market_close", side_sign, size, weight=weight)
            self._sym[symbol].pending.append(o)
            return o
        return None

    def submit_limit_close(self, symbol: str, side_sign: int, size: float, price: float, weight: float = 0.0,
                           raw: bool = False):
        size = self._size_entry(symbol, side_sign, size, raw)
        o = Order("limit_close", side_sign, size, price=price, weight=weight)
        self._sym[symbol].pending.append(o)
        return o

    def _pending_of(self, symbol: str) -> list:
        """Return the live pending-order list for ``symbol`` (used by OrderHandle)."""
        return self._sym[symbol].pending

    def cancel_order(self, symbol: str, order) -> None:
        """Remove a specific order from the pending list (no-op if already gone)."""
        try:
            self._sym[symbol].pending.remove(order)
        except ValueError:
            pass

    def cancel_all(self, symbol: str) -> None:
        self._sym[symbol].pending = []

    # --- higher-TF reads (mirror BacktestEngine, per symbol) ---
    def bars_for(self, symbol: str, tf: str):
        """Completed higher-TF bars for ``symbol`` visible at the current step (no look-ahead)."""
        ms, coarse = self._sym[symbol].tf[tf]
        window_start = self._now - self._now % ms
        # coarse is ts-ascending: bisect instead of rescanning every bar (O(n) per call -> O(n^2)/run).
        return coarse[:bisect.bisect_left(coarse, window_start, key=_BAR_TS)]

    def forming_for(self, symbol: str, tf: str):
        """The still-building coarse bar for ``tf`` / ``symbol`` from base bars seen so far, or None."""
        ms, _ = self._sym[symbol].tf[tf]
        window_start = self._now - self._now % ms
        # base series is ts-ascending: slice [window_start, _now] via bisect, not a full scan per call.
        base = self.bars[symbol]
        lo = bisect.bisect_left(base, window_start, key=_BAR_TS)
        hi = bisect.bisect_right(base, self._now, key=_BAR_TS)
        window = base[lo:hi]
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
        cb = getattr(self.strategy, "on_start", None)
        if cb is not None:
            try:
                cb()
            except Exception:
                logger.exception("strategy on_start failed")
        for i in range(self.n):
            self._step = i  # current bar index — read by is_active() during the fill phase
            cur = {s: self.bars[s][i] for s in self.symbols}
            self._now = cur[self.symbols[0]].ts if self.symbols else 0  # shared aligned timestamp
            # Symbols with finer sub-bars THIS step (and only on the non-gated path) resolve their
            # protective-stop check + resting/market fills together inside _fill_pending_granular, in
            # chronological sub-bar order. Granular + cash_gate is out of scope (documented): the gated
            # shared-cash path always uses coarse bars, so granular symbols there fall back to coarse.
            granular_syms = (
                {s for s in self.symbols if self._sym[s].sub[i]} if not self.cash_gate else set()
            )
            # Protective-stop breach check BEFORE this step's fills/on_bar, for the NON-granular symbols
            # only (granular symbols do their own stop check inside _fill_pending_granular, so we must
            # not double-apply). Only sees stops armed on a prior step, so an entry can't be stopped on
            # its own bar.
            self._check_protective_stops(cur, skip=granular_syms)
            if self.cash_gate:
                self._fill_step_gated(cur)  # cross-symbol shared-cash priority + drop gate
            else:
                for s in self.symbols:
                    if s in granular_syms:
                        self._fill_pending_granular(s, i)  # sub-bar-ordered SL/TP/entry resolution
                    else:
                        self._fill_pending(s, cur[s])  # coarse: fills before decisions => next-open
            self.strategy.index = i
            for s in self.symbols:
                bar = cur[s]
                self._sym[s].price = bar.close
                if bar.funding is not None and self._sym[s].pos.size != 0:
                    self.cash -= funding_charge(self._sym[s].pos.size, bar.close, bar.funding, self.multiplier)
                # Update MAE/MFE extremes for any open position using this bar's high/low (no look-ahead).
                if self._sym[s].pos.size != 0:
                    self._sym[s].hi_since = max(self._sym[s].hi_since, bar.high)
                    self._sym[s].lo_since = min(self._sym[s].lo_since, bar.low)
            self._close_inactive(cur)  # WL removal-day exit: drop any position now out of membership
            self._check_liquidation(cur)
            ts = self.bars[self.symbols[0]][i].ts if self.symbols else 0
            self.strategy._on_step(ts, cur)
            sched = getattr(self.strategy, "schedule", None)
            if sched is not None:
                for _cb in sched.check_due(ts, i):
                    _cb()
            eq = self.equity_now()
            self._equity_peak = max(self._equity_peak, eq)  # track running peak for drawdown
            equity_curve.append(eq)
            equity_ts.append(ts)
            for s in self.symbols:
                pos = self._sym[s].pos
                per_symbol_curve[s].append(self._sym[s].realized + pos.size * (self._sym[s].price - pos.avg_price) * self.multiplier)
        cb = getattr(self.strategy, "on_stop", None)
        if cb is not None:
            try:
                cb()
            except Exception:
                logger.exception("strategy on_stop failed")
        # attribution: realized PnL + open-position mark-to-market, per symbol
        per_symbol = {
            s: self._sym[s].realized + self._sym[s].pos.size * (self._sym[s].price - self._sym[s].pos.avg_price) * self.multiplier
            for s in self.symbols
        }
        return PortfolioResult(self.trades, equity_curve, self.equity_now(), per_symbol_pnl=per_symbol, per_symbol_curves=per_symbol_curve, equity_ts=equity_ts)

    def _fill_pending(self, symbol: str, bar: Bar) -> None:
        still = []
        for o in self._sym[symbol].pending:
            fp = order_fill_price(o, bar)
            if fp is None:
                still.append(o)               # rest until triggered
            else:
                pos = self._sym[symbol].pos
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
                    # Arm this entry's protective stop once the position is actually open. Armed
                    # in the fill phase (top of the step) — the stop-breach check runs in the NEXT
                    # step's fill phase, so an entry can't be stopped on its own entry bar.
                    if o.stop is not None and self._sym[symbol].pos.size != 0:
                        self._sym[symbol].stop = o.stop
        self._sym[symbol].pending = still

    def _cancel_protective_exits(self, symbol: str, was_long: bool) -> None:
        """OCO: when a protective stop closes a position inside the granular path, cancel any pending
        resting EXIT limit that was protecting it (a take-profit) so it can't re-fire on a later
        sub-bar and accidentally open a fresh opposite-side position. The take-profit for a long is a
        ``limit``/``limit_close`` SELL (side < 0); for a short it's a ``limit``/``limit_close`` BUY
        (side > 0). Pending entry orders and same-direction adds are left untouched (they're not OCO
        siblings of the stop)."""
        closing_side = -1 if was_long else 1  # the side that REDUCES/closes the stopped-out position
        self._sym[symbol].pending = [
            o for o in self._sym[symbol].pending
            if not (o.kind in ("limit", "limit_close") and o.side == closing_side)
        ]

    def _fill_pending_granular(self, symbol: str, i: int) -> None:
        """Granular replacement for ``_check_protective_stops(symbol) + _fill_pending(symbol)`` at coarse
        step ``i``, used WHEN finer sub-bars exist for this symbol/step.

        Walk the step's sub-bars in chronological order. At each sub-bar build the set of events that
        trigger at THIS sub-bar: the armed protective stop (if breached) plus every pending order whose
        ``order_fill_price`` fires on this sub-bar. Apply them with a deterministic intra-sub-bar order
        (protective stop first — the SL-vs-TP question is the cross-sub-bar TIME ordering, not the
        within-sub-bar tiebreak), using the SAME opening gates as ``_fill_pending`` and arming
        ``self._stop`` on opening fills. Filled orders leave ``self._pending``; a protective-stop close
        clears ``self._stop``. Orders that never trigger across all sub-bars stay pending. Market /
        market_close fill on the FIRST sub-bar (its open / close), exactly like the coarse path's
        next-open. KEY: a take-profit limit and a protective stop on the same coarse bar resolve by
        which sub-bar comes first.
        """
        sub_bars = self._sym[symbol].sub[i]
        for sub in sub_bars:
            # 1) Protective stop first within this sub-bar (only sees a stop armed on a PRIOR step or a
            #    prior sub-bar — same "can't stop on its own entry bar" guarantee as the coarse path,
            #    since an entry filled in THIS sub-bar arms the stop after this point and is breach-
            #    checked from the next sub-bar / step).
            stop = self._sym[symbol].stop
            pos = self._sym[symbol].pos
            if stop is not None and pos.size != 0:
                was_long = pos.size > 0
                if was_long and sub.low <= stop:  # long protective sell-stop
                    self._apply_fill(symbol, -1, abs(pos.size), stop, sub.ts)
                    self._sym[symbol].stop = None
                    self._cancel_protective_exits(symbol, was_long)  # OCO: drop the sibling TP exit
                elif (not was_long) and sub.high >= stop:  # short protective buy-stop
                    self._apply_fill(symbol, +1, abs(pos.size), stop, sub.ts)
                    self._sym[symbol].stop = None
                    self._cancel_protective_exits(symbol, was_long)  # OCO: drop the sibling TP exit
            # 2) Resting / market orders that trigger on THIS sub-bar, in pending order.
            still = []
            for o in self._sym[symbol].pending:
                fp = order_fill_price(o, sub)  # ratchets a trailing extreme in place per sub-bar
                if fp is None:
                    still.append(o)  # rest until a later sub-bar (or step) triggers it
                    continue
                pos = self._sym[symbol].pos
                opening = pos.size == 0 or (pos.size > 0) == (o.side > 0)
                if opening and not self.is_active(symbol):
                    continue  # inactive member: drop opening/adding fills (reduces still apply)
                if pos.size == 0 and self._at_open_cap():
                    continue  # MaxOpenPositions cap reached: drop new-symbol opens
                if pos.size == 0 and o.side > 0 and self._at_long_cap():
                    continue  # max_open_long cap reached
                if pos.size == 0 and o.side < 0 and self._at_short_cap():
                    continue  # max_open_short cap reached
                fill_size = o.size
                if self.volume_limit:
                    allowed = self.volume_limit * sub.volume
                    if fill_size > allowed:
                        dropped_size = fill_size - allowed
                        self.dropped.append((symbol, "volume_cap", dropped_size, 0.0))
                        fill_size = allowed
                if fill_size > 0:
                    self._apply_fill(symbol, o.side, fill_size, fp, sub.ts, is_maker=o.kind == "limit")
                    # Arm this entry's protective stop once the position is open. Armed within this
                    # sub-bar's fill phase; breach-checked from the NEXT sub-bar / step (above).
                    if o.stop is not None and self._sym[symbol].pos.size != 0:
                        self._sym[symbol].stop = o.stop
            self._sym[symbol].pending = still

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
            for o in self._sym[s].pending:
                fp = order_fill_price(o, cur[s])
                if fp is None:
                    still.append(o)
                    continue
                pos = self._sym[s].pos
                increasing = pos.size == 0 or (pos.size > 0) == (o.side > 0)
                if increasing and not self.is_active(s):
                    continue              # inactive member: drop opening/adding fills before the cash gate
                (opens if increasing else frees).append((s, o, fp, seq))
                seq += 1
            self._sym[s].pending = still
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
            if self._sym[s].pos.size == 0 and self._at_open_cap():
                self.dropped.append((s, o.kind, o.size, o.weight))
                continue
            # Per-direction caps: re-checked live; only block new-symbol opens
            if self._sym[s].pos.size == 0 and o.side > 0 and self._at_long_cap():
                self.dropped.append((s, o.kind, o.size, o.weight))
                continue
            if self._sym[s].pos.size == 0 and o.side < 0 and self._at_short_cap():
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
            # Arm this entry's protective stop (see _fill_pending: armed at fill, checked next step).
            if o.stop is not None and self._sym[s].pos.size != 0:
                self._sym[s].stop = o.stop

    def _apply_fill(self, symbol: str, side_sign: int, size: float, price: float, ts: int,
                    is_maker: bool = False) -> None:
        price = adverse_fill_price(price, side_sign, self.slippage)
        rate = self.maker_fee if is_maker else self.taker_fee
        fee = _fee(size, price, rate, self.multiplier)
        delta = side_sign * size
        st = self._sym[symbol]
        pos = st.pos
        self.cash -= fee                                  # transaction cost
        self.cash -= delta * price * self.multiplier      # signed notional moves cash in every case
        out = compute_fill(pos.size, pos.avg_price, side_sign, size, price, self.multiplier)

        if out.kind == "open":
            pos.size = out.new_size
            pos.avg_price = out.new_avg_px
            st.entry_fee = fee
            st.entry_ts = ts
            st.hi_since = price                           # reset excursion extremes to entry
            st.lo_since = price
            self._fire_on_fill(symbol, side_sign, size, price, fee, ts, is_maker)
            return
        if out.kind == "add":
            pos.size = out.new_size
            pos.avg_price = out.new_avg_px
            st.entry_fee += fee
            self._fire_on_fill(symbol, side_sign, size, price, fee, ts, is_maker)
            return

        # reduce / close / flip
        entry_fee_portion = st.entry_fee * out.portion
        exit_fee_portion = fee * (out.closing_qty / size) if delta != 0 else 0.0
        st.realized += out.realized_pnl
        entry = out.entry_avg_px                          # avg price of the closed portion
        hi, lo = st.hi_since, st.lo_since
        if entry != 0:
            if pos.size > 0:                              # long
                mfe = (hi - entry) / entry
                mae = (lo - entry) / entry
            else:                                         # short
                mfe = (entry - lo) / entry
                mae = (entry - hi) / entry
        else:
            mfe, mae = 0.0, 0.0
        self.trades.append(
            Trade(
                entry_price=out.entry_avg_px,
                exit_price=price,
                size=out.closing_qty,
                pnl=out.realized_pnl,
                fees=entry_fee_portion + exit_fee_portion,
                entry_ts=st.entry_ts,
                exit_ts=ts,
                symbol=symbol,
                mae=mae,
                mfe=mfe,
            )
        )
        pos.size = out.new_size
        pos.avg_price = out.new_avg_px
        if out.kind == "reduce":
            st.entry_fee -= entry_fee_portion
        elif out.kind == "flip":
            st.entry_fee = fee * (out.leftover / size)
            st.entry_ts = ts
            st.hi_since = price
            st.lo_since = price
            st.stop = None                                # old stop belonged to the closed position
        else:  # close -> flat
            st.entry_fee = 0.0
            st.entry_ts = 0
            st.stop = None
        self._fire_on_fill(symbol, side_sign, size, price, fee, ts, is_maker)

    def _fire_on_fill(self, symbol: str, side_sign: int, size: float, price: float, fee: float,
                      ts: int, is_maker: bool) -> None:
        """Fire strategy.on_fill(fill) if present; guards via getattr so old PortfolioStrategy
        subclasses (which don't have on_fill) are unaffected. Exceptions are logged but never
        propagate — a strategy bug must not break the deterministic sim loop."""
        cb = getattr(self.strategy, "on_fill", None)
        if cb is None:
            return
        fill = Fill(side=side_sign, size=size, price=price, fee=fee, ts=ts,
                    is_maker=is_maker, symbol=symbol)
        try:
            cb(fill)
        except Exception:
            logger.exception("strategy on_fill failed")
