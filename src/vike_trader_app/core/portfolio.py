"""Multi-asset / portfolio backtesting on one shared cash account.

Additive sibling to the single-symbol engine. ``PortfolioEngine`` takes aligned
per-symbol bar series and steps timestamp-by-timestamp; orders submitted during a
step fill at each symbol's NEXT bar open (no look-ahead), mirroring the single-symbol
engine. Equity = cash + sum(position size * last close).
"""

from dataclasses import dataclass, field

from .broker_sim import adverse_fill_price, fee as _fee
from .model import Bar, Position, Trade
from .orders import Order, order_fill_price


@dataclass
class PortfolioResult:
    """Outcome of a portfolio backtest run."""

    trades: list[Trade]
    equity_curve: list[float]
    final_equity: float
    per_symbol_pnl: dict = field(default_factory=dict)  # realized + unrealized PnL per symbol


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

    def buy(self, symbol: str, size: float) -> None:
        self._engine.submit(symbol, +1, size)

    def sell(self, symbol: str, size: float) -> None:
        self._engine.submit(symbol, -1, size)

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

    def __init__(self) -> None:
        super().__init__()
        self._hist: dict[str, list[float]] = {}

    def score(self, symbol: str, history: list[float]):
        """Return a comparable score for ``symbol`` (higher = better), or None to skip."""
        raise NotImplementedError

    def weights(self, winners: list[str]) -> dict:
        """Weights for the selected symbols (default: equal weight)."""
        w = 1.0 / len(winners) if winners else 0.0
        return {s: w for s in winners}

    def on_bar(self, ts: int, bars: dict) -> None:
        for sym, bar in bars.items():
            self._hist.setdefault(sym, []).append(bar.close)
        if self.index % self.rebalance_every != 0:
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
                 taker_fee: float | None = None, multiplier: float = 1.0):
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
        self.trades: list[Trade] = []
        self._realized: dict[str, float] = {s: 0.0 for s in self.symbols}  # realized PnL per symbol
        self._pos: dict[str, Position] = {s: Position() for s in self.symbols}
        self._pending: dict[str, list[Order]] = {s: [] for s in self.symbols}
        self._entry_fee: dict[str, float] = {s: 0.0 for s in self.symbols}
        self._entry_ts: dict[str, int] = {s: 0 for s in self.symbols}
        self._price: dict[str, float] = {
            s: bars_by_symbol[s][0].open if self.n else 0.0 for s in self.symbols
        }
        strategy._engine = self

    # --- reads exposed to the strategy ---
    def position_of(self, symbol: str) -> Position:
        return self._pos[symbol]

    def price_of(self, symbol: str) -> float:
        return self._price[symbol]

    def equity_now(self) -> float:
        return self.cash + sum(self._pos[s].size * self._price[s] * self.multiplier for s in self.symbols)

    # --- order intake ---
    def submit(self, symbol: str, side_sign: int, size: float) -> None:
        self._pending[symbol].append(Order("market", side_sign, size))

    def submit_close(self, symbol: str) -> None:
        pos = self._pos[symbol]
        if pos.size != 0:
            side = -1 if pos.size > 0 else 1
            self._pending[symbol].append(Order("market", side, abs(pos.size)))

    def submit_limit(self, symbol: str, side_sign: int, size: float, price: float) -> None:
        self._pending[symbol].append(Order("limit", side_sign, size, price=price))

    def submit_stop(self, symbol: str, side_sign: int, size: float, price: float) -> None:
        self._pending[symbol].append(Order("stop", side_sign, size, price=price))

    def submit_trailing(self, symbol: str, side_sign: int, size: float, trail: float) -> None:
        self._pending[symbol].append(Order("trailing", side_sign, size, trail=trail,
                                           extreme=self._price[symbol]))

    def cancel_all(self, symbol: str) -> None:
        self._pending[symbol] = []

    # --- run loop ---
    def run(self) -> PortfolioResult:
        equity_curve: list[float] = []
        for i in range(self.n):
            for s in self.symbols:
                bar = self.bars[s][i]
                self._fill_pending(s, bar)  # fills before decisions => next-open
            self.strategy.index = i
            cur = {}
            for s in self.symbols:
                bar = self.bars[s][i]
                self._price[s] = bar.close
                cur[s] = bar
            ts = self.bars[self.symbols[0]][i].ts if self.symbols else 0
            self.strategy.on_bar(ts, cur)
            equity_curve.append(self.equity_now())
        # attribution: realized PnL + open-position mark-to-market, per symbol
        per_symbol = {
            s: self._realized[s] + self._pos[s].size * (self._price[s] - self._pos[s].avg_price) * self.multiplier
            for s in self.symbols
        }
        return PortfolioResult(self.trades, equity_curve, self.equity_now(), per_symbol_pnl=per_symbol)

    def _fill_pending(self, symbol: str, bar: Bar) -> None:
        still = []
        for o in self._pending[symbol]:
            fp = order_fill_price(o, bar)
            if fp is None:
                still.append(o)               # rest until triggered
            else:
                self._apply_fill(symbol, o.side, o.size, fp, bar.ts, is_maker=o.kind == "limit")
        self._pending[symbol] = still

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
        else:  # flat
            pos.size = 0.0
            pos.avg_price = 0.0
            self._entry_fee[symbol] = 0.0
            self._entry_ts[symbol] = 0
