"""Feature/label construction for ML strategies.

Pure, aligned-list helpers (``None`` warm-up, matching the indicator convention) so a
model trains on the same point-in-time data the strategy will see at decision time.
"""


def make_features(closes, lookback: int):
    """Per-bar feature tuples = the last ``lookback`` simple returns ending at that bar.

    Returns a list aligned to ``closes`` with ``None`` until ``lookback`` returns exist.
    """
    n = len(closes)
    rets = [None] * n
    for i in range(1, n):
        rets[i] = closes[i] / closes[i - 1] - 1.0 if closes[i - 1] else 0.0
    out: list = [None] * n
    for i in range(lookback, n):
        window = rets[i - lookback + 1 : i + 1]
        if all(r is not None for r in window):
            out[i] = tuple(window)
    return out


def make_labels(closes, horizon: int = 1):
    """Per-bar label = sign of the forward return over ``horizon`` bars (+1 / -1).

    ``None`` for the final ``horizon`` bars (no future to label).
    """
    n = len(closes)
    out: list = [None] * n
    for i in range(n - horizon):
        out[i] = 1.0 if closes[i + horizon] > closes[i] else -1.0
    return out
