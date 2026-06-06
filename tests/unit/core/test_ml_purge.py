"""Phase 6: ML walk-forward purge + embargo (no label-horizon leakage across train/test)."""

import math

from vike_trader_app.analysis.validation import walk_forward_splits
from vike_trader_app.core.model import Bar
from vike_trader_app.ml.dataset import make_features, make_labels
from vike_trader_app.ml.walkforward import walk_forward_ml


def _bars(n):
    closes = [100.0 + 10.0 * math.sin(i / 5.0) for i in range(n)]
    bars = [Bar(ts=i * 60_000, open=c, high=c + 0.1, low=c - 0.1, close=c)
            for i, c in enumerate(closes)]
    return bars, closes


def test_walk_forward_ml_purges_label_horizon_leakage():
    bars, closes = _bars(180)
    lookback, horizon, n_splits = 4, 6, 3
    feats = make_features(closes, lookback)
    labels = make_labels(closes, horizon)

    seen: list[int] = []

    def train_fn(x, _y):
        seen.append(len(x))
        return lambda _f: 1.0

    walk_forward_ml(bars, lookback, horizon, train_fn, n_splits=n_splits)

    splits = walk_forward_splits(len(bars), n_splits)
    assert len(seen) == len(splits)
    for (tr_s, tr_e, te_s, _te_e), n_seen in zip(splits, seen, strict=True):
        purged = sum(1 for j in range(tr_s, max(tr_s, te_s - horizon))
                     if feats[j] is not None and labels[j] is not None)
        unpurged = sum(1 for j in range(tr_s, tr_e)
                       if feats[j] is not None and labels[j] is not None)
        assert n_seen == purged       # the training set stops `horizon` bars before the test window
        assert purged < unpurged      # and the leaky tail was actually dropped


def test_embargo_drops_additional_training_samples():
    bars, _ = _bars(180)
    counts: dict[str, int] = {}

    def make_fn(key):
        counts.setdefault(key, 0)

        def fn(x, _y):
            counts[key] += len(x)
            return lambda _f: 1.0

        return fn

    walk_forward_ml(bars, 4, 6, make_fn("e0"), n_splits=3, embargo=0)
    walk_forward_ml(bars, 4, 6, make_fn("e20"), n_splits=3, embargo=20)
    assert counts["e20"] < counts["e0"]  # a larger embargo trains on strictly fewer rows
