"""A catalogue of common indicators as vetted, insertable Python snippets.

Each entry carries a self-contained helper the user can drop into a Strategy (they already
accumulate ``self.closes`` etc. — see the example strategies). Every snippet is preflight-clean
(``core.sandbox.preflight``): only allowed imports, no forbidden names, no dunder access — so
inserting one never trips the sandbox gate. Pure data; the UI (``ui/indicators.py``) renders it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Indicator:
    """One catalogue entry: a name, grouping, blurb, params summary, and an insertable snippet."""

    name: str
    category: str
    description: str
    params: str
    snippet: str


CATALOG: list[Indicator] = [
    Indicator(
        "SMA", "Trend", "Simple moving average of the last n values.", "n",
        'def sma(values, n):\n'
        '    """Simple moving average of the last n values (None until n available)."""\n'
        '    return sum(values[-n:]) / n if len(values) >= n else None\n',
    ),
    Indicator(
        "EMA", "Trend", "Exponential moving average (recent values weighted more).", "n",
        'def ema(values, n):\n'
        '    """Exponential moving average; None until n values exist."""\n'
        '    if len(values) < n:\n'
        '        return None\n'
        '    k = 2.0 / (n + 1)\n'
        '    e = sum(values[:n]) / n\n'
        '    for v in values[n:]:\n'
        '        e = v * k + e * (1 - k)\n'
        '    return e\n',
    ),
    Indicator(
        "RSI", "Momentum", "Wilder Relative Strength Index over the last n closes (0–100).", "n=14",
        'def rsi(closes, n=14):\n'
        '    """Relative Strength Index (0-100); None until n+1 closes."""\n'
        '    if len(closes) < n + 1:\n'
        '        return None\n'
        '    gains = losses = 0.0\n'
        '    for i in range(len(closes) - n, len(closes)):\n'
        '        d = closes[i] - closes[i - 1]\n'
        '        gains += max(d, 0.0)\n'
        '        losses += max(-d, 0.0)\n'
        '    if losses == 0:\n'
        '        return 100.0\n'
        '    rs = (gains / n) / (losses / n)\n'
        '    return 100.0 - 100.0 / (1 + rs)\n',
    ),
    Indicator(
        "Bollinger Bands", "Volatility", "(lower, mid, upper) = SMA ± k·stdev over n closes.",
        "n=20, k=2.0",
        'import statistics\n\n'
        'def bollinger(closes, n=20, k=2.0):\n'
        '    """(lower, mid, upper) Bollinger Bands; None until n closes."""\n'
        '    if len(closes) < n:\n'
        '        return None\n'
        '    window = closes[-n:]\n'
        '    mid = sum(window) / n\n'
        '    sd = statistics.pstdev(window)\n'
        '    return (mid - k * sd, mid, mid + k * sd)\n',
    ),
    Indicator(
        "ATR", "Volatility", "Average True Range over the last n bars (needs highs/lows).", "n=14",
        'def atr(highs, lows, closes, n=14):\n'
        '    """Average true range over the last n bars; None until n+1 bars."""\n'
        '    if len(closes) < n + 1:\n'
        '        return None\n'
        '    trs = []\n'
        '    for i in range(len(closes) - n, len(closes)):\n'
        '        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),\n'
        '                 abs(lows[i] - closes[i - 1]))\n'
        '        trs.append(tr)\n'
        '    return sum(trs) / n\n',
    ),
    Indicator(
        "MACD line", "Trend", "EMA(fast) − EMA(slow) of closes.", "fast=12, slow=26",
        'def macd_line(closes, fast=12, slow=26):\n'
        '    """MACD line = EMA(fast) - EMA(slow); None until slow closes."""\n'
        '    def _ema(vals, n):\n'
        '        if len(vals) < n:\n'
        '            return None\n'
        '        k = 2.0 / (n + 1)\n'
        '        e = sum(vals[:n]) / n\n'
        '        for v in vals[n:]:\n'
        '            e = v * k + e * (1 - k)\n'
        '        return e\n'
        '    f, s = _ema(closes, fast), _ema(closes, slow)\n'
        '    return None if f is None or s is None else f - s\n',
    ),
    Indicator(
        "Stochastic %K", "Momentum", "Where the close sits in the last n high-low range (0–100).",
        "n=14",
        'def stoch_k(highs, lows, closes, n=14):\n'
        '    """Stochastic %K over the last n bars (0-100); None until n bars."""\n'
        '    if len(closes) < n:\n'
        '        return None\n'
        '    hh, ll = max(highs[-n:]), min(lows[-n:])\n'
        '    return 100.0 * (closes[-1] - ll) / (hh - ll) if hh > ll else 50.0\n',
    ),
    Indicator(
        "VWAP", "Volume", "Volume-weighted average price over the last n bars.", "n=20",
        'def vwap(closes, volumes, n=20):\n'
        '    """Volume-weighted average price over the last n bars; None until n bars."""\n'
        '    if len(closes) < n or sum(volumes[-n:]) == 0:\n'
        '        return None\n'
        '    return sum(c * v for c, v in zip(closes[-n:], volumes[-n:])) / sum(volumes[-n:])\n',
    ),
    Indicator(
        "ROC", "Momentum", "Rate of change — % return over the last n bars.", "n=10",
        'def roc(closes, n=10):\n'
        '    """Rate of change (% return over n bars); None until n+1 closes."""\n'
        '    if len(closes) < n + 1 or closes[-n - 1] == 0:\n'
        '        return None\n'
        '    return (closes[-1] / closes[-n - 1] - 1.0) * 100.0\n',
    ),
    Indicator(
        "Z-score", "Statistics", "How many stdevs the last close is from its n-bar mean.", "n=20",
        'import statistics\n\n'
        'def zscore(closes, n=20):\n'
        '    """Z-score of the last close vs the last n closes; None until n closes."""\n'
        '    if len(closes) < n:\n'
        '        return None\n'
        '    window = closes[-n:]\n'
        '    mu = sum(window) / n\n'
        '    sd = statistics.pstdev(window)\n'
        '    return (closes[-1] - mu) / sd if sd > 0 else 0.0\n',
    ),
]
