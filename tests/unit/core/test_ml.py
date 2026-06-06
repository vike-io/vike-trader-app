"""ML strategy support: features/labels + train-inside-walk-forward."""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.ml.dataset import make_features, make_labels
from vike_trader_app.ml.walkforward import walk_forward_ml


def _rising(n=24):
    return [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1.0) for i in range(n)]


def test_make_features_warmup_then_return_window():
    closes = [100, 110, 121, 133.1]  # +10% each step
    feats = make_features(closes, lookback=2)
    assert feats[0] is None and feats[1] is None  # need lookback+1 closes
    assert feats[2] == pytest.approx((0.1, 0.1))   # returns at idx1 and idx2
    assert len(feats) == len(closes)


def test_make_labels_sign_of_forward_return():
    closes = [100, 101, 100, 102]
    labels = make_labels(closes, horizon=1)
    assert labels[0] == 1.0    # 101 > 100
    assert labels[1] == -1.0   # 100 < 101
    assert labels[-1] is None  # no future bar


def test_walk_forward_ml_trains_and_runs_oos():
    bars = _rising()

    def train_fn(x_train, y_train):
        # majority-vote "model": predict the dominant label sign from training
        bias = sum(y_train)
        return lambda features: 1.0 if bias >= 0 else -1.0

    rep = walk_forward_ml(bars, lookback=2, horizon=1, train_fn=train_fn, n_splits=3)
    assert len(rep.windows) == 3
    # rising market -> labels all +1 -> model goes long -> positive stitched OOS return
    assert rep.oos_return > 0
    assert all(w.oos_result is not None for w in rep.windows)
