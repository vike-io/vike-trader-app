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


_MA_CROSS = 'from vike_trader_app.core.strategy import Strategy\n\n\nclass MaCrossover(Strategy):\n    """Trend-follower: long when the fast SMA crosses above the slow SMA, flat on the down-cross."""\n\n    WARMUP = 30\n    fast = 10\n    slow = 30\n    PARAM_GRID = {"fast": [5, 10], "slow": [20, 30]}\n\n    def __init__(self):\n        self.closes = []\n\n    def on_bar(self, bar):\n        self.closes.append(bar.close)\n        if len(self.closes) <= self.slow:\n            return\n        f = sum(self.closes[-self.fast:]) / self.fast\n        s = sum(self.closes[-self.slow:]) / self.slow\n        fp = sum(self.closes[-self.fast - 1:-1]) / self.fast\n        sp = sum(self.closes[-self.slow - 1:-1]) / self.slow\n        if fp <= sp and f > s and self.position(bar.symbol).size == 0:\n            self.buy(bar.symbol, 1.0)\n        elif fp >= sp and f < s and self.position(bar.symbol).size > 0:\n            self.close(bar.symbol)\n'

_RSI_REVERSION = 'from vike_trader_app.core.strategy import Strategy\n\n\nclass RsiMeanReversion(Strategy):\n    """Mean-reversion: buy when RSI dips below `low`, exit when it recovers above `high`."""\n\n    WARMUP = 20\n    n = 14\n    low = 30.0\n    high = 55.0\n\n    def __init__(self):\n        self.closes = []\n\n    def _rsi(self):\n        c = self.closes\n        if len(c) < self.n + 1:\n            return None\n        gains = losses = 0.0\n        for i in range(len(c) - self.n, len(c)):\n            d = c[i] - c[i - 1]\n            gains += max(d, 0.0)\n            losses += max(-d, 0.0)\n        if losses == 0:\n            return 100.0\n        rs = (gains / self.n) / (losses / self.n)\n        return 100.0 - 100.0 / (1 + rs)\n\n    def on_bar(self, bar):\n        self.closes.append(bar.close)\n        r = self._rsi()\n        if r is None:\n            return\n        if r < self.low and self.position(bar.symbol).size == 0:\n            self.buy(bar.symbol, 1.0)\n        elif r > self.high and self.position(bar.symbol).size > 0:\n            self.close(bar.symbol)\n'

_BOLLINGER = 'import statistics\n\nfrom vike_trader_app.core.strategy import Strategy\n\n\nclass BollingerReversion(Strategy):\n    """Mean-reversion: buy below the lower Bollinger band, exit back at the mid band."""\n\n    WARMUP = 25\n    n = 20\n    k = 2.0\n\n    def __init__(self):\n        self.closes = []\n\n    def on_bar(self, bar):\n        self.closes.append(bar.close)\n        if len(self.closes) < self.n:\n            return\n        window = self.closes[-self.n:]\n        mid = sum(window) / self.n\n        lower = mid - self.k * statistics.pstdev(window)\n        if bar.close < lower and self.position(bar.symbol).size == 0:\n            self.buy(bar.symbol, 1.0)\n        elif bar.close >= mid and self.position(bar.symbol).size > 0:\n            self.close(bar.symbol)\n'

_DONCHIAN = 'from vike_trader_app.core.strategy import Strategy\n\n\nclass DonchianBreakout(Strategy):\n    """Breakout: buy on a new n-bar high, exit on a new m-bar low (Donchian channel)."""\n\n    WARMUP = 25\n    n = 20\n    m = 10\n\n    def __init__(self):\n        self.highs = []\n        self.lows = []\n\n    def on_bar(self, bar):\n        self.highs.append(bar.high)\n        self.lows.append(bar.low)\n        if len(self.highs) <= self.n:\n            return\n        hh = max(self.highs[-self.n - 1:-1])\n        ll = min(self.lows[-self.m - 1:-1])\n        if bar.high >= hh and self.position(bar.symbol).size == 0:\n            self.buy(bar.symbol, 1.0)\n        elif bar.low <= ll and self.position(bar.symbol).size > 0:\n            self.close(bar.symbol)\n'

_MOMENTUM = 'from vike_trader_app.core.strategy import Strategy\n\n\nclass MomentumRoc(Strategy):\n    """Momentum: long while the n-bar rate-of-change is positive, flat when it turns negative."""\n\n    WARMUP = 15\n    n = 10\n\n    def __init__(self):\n        self.closes = []\n\n    def on_bar(self, bar):\n        self.closes.append(bar.close)\n        if len(self.closes) <= self.n:\n            return\n        roc = self.closes[-1] / self.closes[-self.n - 1] - 1.0 if self.closes[-self.n - 1] else 0.0\n        if roc > 0 and self.position(bar.symbol).size == 0:\n            self.buy(bar.symbol, 1.0)\n        elif roc < 0 and self.position(bar.symbol).size > 0:\n            self.close(bar.symbol)\n'


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
