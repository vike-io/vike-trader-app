"""A gallery of complete, ready-to-run strategy templates.

Each ``code`` is a full, PREFLIGHT-CLEAN ``Strategy`` subclass that loads straight into the
Studio editor and backtests immediately (then validate it — the honesty wedge). They use the
established no-``super().__init__`` pattern (the engine injects ``_engine``/``index``) so they
pass the sandbox AST gate. Pure data; the UI (``ui/templates.py``) renders it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyTemplate:
    """One gallery entry: name, category, blurb, and a complete strategy ``code`` string."""

    name: str
    category: str
    description: str
    code: str


_MA_CROSS = '''from vike_trader_app.core.strategy import Strategy


class MaCrossover(Strategy):
    """Trend-follower: long when the fast SMA crosses above the slow SMA, flat on the down-cross."""

    WARMUP = 30
    fast = 10
    slow = 30
    PARAM_GRID = {"fast": [5, 10], "slow": [20, 30]}

    def __init__(self):
        self.closes = []

    def on_bar(self, bar):
        self.closes.append(bar.close)
        if len(self.closes) <= self.slow:
            return
        f = sum(self.closes[-self.fast:]) / self.fast
        s = sum(self.closes[-self.slow:]) / self.slow
        fp = sum(self.closes[-self.fast - 1:-1]) / self.fast
        sp = sum(self.closes[-self.slow - 1:-1]) / self.slow
        if fp <= sp and f > s and self.position.size == 0:
            self.buy(1.0)
        elif fp >= sp and f < s and self.position.size > 0:
            self.close()
'''

_RSI_REVERSION = '''from vike_trader_app.core.strategy import Strategy


class RsiMeanReversion(Strategy):
    """Mean-reversion: buy when RSI dips below `low`, exit when it recovers above `high`."""

    WARMUP = 20
    n = 14
    low = 30.0
    high = 55.0

    def __init__(self):
        self.closes = []

    def _rsi(self):
        c = self.closes
        if len(c) < self.n + 1:
            return None
        gains = losses = 0.0
        for i in range(len(c) - self.n, len(c)):
            d = c[i] - c[i - 1]
            gains += max(d, 0.0)
            losses += max(-d, 0.0)
        if losses == 0:
            return 100.0
        rs = (gains / self.n) / (losses / self.n)
        return 100.0 - 100.0 / (1 + rs)

    def on_bar(self, bar):
        self.closes.append(bar.close)
        r = self._rsi()
        if r is None:
            return
        if r < self.low and self.position.size == 0:
            self.buy(1.0)
        elif r > self.high and self.position.size > 0:
            self.close()
'''

_BOLLINGER = '''import statistics

from vike_trader_app.core.strategy import Strategy


class BollingerReversion(Strategy):
    """Mean-reversion: buy below the lower Bollinger band, exit back at the mid band."""

    WARMUP = 25
    n = 20
    k = 2.0

    def __init__(self):
        self.closes = []

    def on_bar(self, bar):
        self.closes.append(bar.close)
        if len(self.closes) < self.n:
            return
        window = self.closes[-self.n:]
        mid = sum(window) / self.n
        lower = mid - self.k * statistics.pstdev(window)
        if bar.close < lower and self.position.size == 0:
            self.buy(1.0)
        elif bar.close >= mid and self.position.size > 0:
            self.close()
'''

_DONCHIAN = '''from vike_trader_app.core.strategy import Strategy


class DonchianBreakout(Strategy):
    """Breakout: buy on a new n-bar high, exit on a new m-bar low (Donchian channel)."""

    WARMUP = 25
    n = 20
    m = 10

    def __init__(self):
        self.highs = []
        self.lows = []

    def on_bar(self, bar):
        self.highs.append(bar.high)
        self.lows.append(bar.low)
        if len(self.highs) <= self.n:
            return
        hh = max(self.highs[-self.n - 1:-1])
        ll = min(self.lows[-self.m - 1:-1])
        if bar.high >= hh and self.position.size == 0:
            self.buy(1.0)
        elif bar.low <= ll and self.position.size > 0:
            self.close()
'''

_MOMENTUM = '''from vike_trader_app.core.strategy import Strategy


class MomentumRoc(Strategy):
    """Momentum: long while the n-bar rate-of-change is positive, flat when it turns negative."""

    WARMUP = 15
    n = 10

    def __init__(self):
        self.closes = []

    def on_bar(self, bar):
        self.closes.append(bar.close)
        if len(self.closes) <= self.n:
            return
        roc = self.closes[-1] / self.closes[-self.n - 1] - 1.0 if self.closes[-self.n - 1] else 0.0
        if roc > 0 and self.position.size == 0:
            self.buy(1.0)
        elif roc < 0 and self.position.size > 0:
            self.close()
'''


TEMPLATES: list[StrategyTemplate] = [
    StrategyTemplate("MA crossover", "Trend",
                     "Fast/slow SMA crossover — the classic trend-follower.", _MA_CROSS),
    StrategyTemplate("RSI mean-reversion", "Mean reversion",
                     "Buy oversold (RSI < 30), exit on recovery.", _RSI_REVERSION),
    StrategyTemplate("Bollinger reversion", "Mean reversion",
                     "Buy below the lower band, exit at the mid band.", _BOLLINGER),
    StrategyTemplate("Donchian breakout", "Breakout",
                     "Buy n-bar highs, exit m-bar lows (channel breakout).", _DONCHIAN),
    StrategyTemplate("Momentum (ROC)", "Momentum",
                     "Hold while n-bar momentum is positive.", _MOMENTUM),
]
