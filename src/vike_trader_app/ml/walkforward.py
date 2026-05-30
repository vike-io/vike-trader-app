"""Walk-forward ML: train a model on each train window, predict out-of-sample.

The honest way to backtest an ML strategy — the model only ever predicts on data it
was not trained on. ``train_fn(X_train, y_train) -> (features -> signal)`` is supplied
by the user (any library, or none); we handle the point-in-time plumbing and stitch
the out-of-sample windows into one equity curve.
"""

from dataclasses import dataclass, field

from ..analysis.metrics import sharpe
from ..analysis.validation import walk_forward_splits
from ..core.engine import BacktestEngine
from .dataset import make_features, make_labels
from .strategy import MLStrategy


@dataclass
class MLWindow:
    train_range: tuple
    test_range: tuple
    n_train: int
    oos_return: float
    oos_result: object = field(default=None, repr=False)


@dataclass
class MLReport:
    windows: list
    oos_equity_curve: list
    oos_return: float
    oos_sharpe: float


def walk_forward_ml(
    bars,
    lookback: int,
    horizon: int,
    train_fn,
    n_splits: int = 4,
    fee_rate: float = 0.0,
    cash: float = 10_000.0,
):
    """Train ``train_fn`` per walk-forward window, run its predictor OOS, stitch results."""
    closes = [b.close for b in bars]
    feats = make_features(closes, lookback)
    labels = make_labels(closes, horizon)
    splits = walk_forward_splits(len(bars), n_splits)

    windows: list[MLWindow] = []
    stitched: list[float] = []
    equity = cash
    for tr_s, tr_e, te_s, te_e in splits:
        x_train, y_train = [], []
        for j in range(tr_s, tr_e):
            if feats[j] is not None and labels[j] is not None:
                x_train.append(feats[j])
                y_train.append(labels[j])
        predict = train_fn(x_train, y_train)

        strat = MLStrategy()
        strat.feats = feats[te_s:te_e]
        strat.predict = predict
        oos = BacktestEngine(bars[te_s:te_e], strat, fee_rate=fee_rate, cash=cash).run()

        start = equity
        for v in oos.equity_curve:
            stitched.append(start * (v / cash))
        equity = start * (oos.final_equity / cash)
        windows.append(
            MLWindow(
                train_range=(tr_s, tr_e),
                test_range=(te_s, te_e),
                n_train=len(x_train),
                oos_return=(oos.final_equity / cash) - 1.0,
                oos_result=oos,
            )
        )
    return MLReport(
        windows=windows,
        oos_equity_curve=stitched,
        oos_return=(equity / cash) - 1.0,
        oos_sharpe=sharpe(stitched),
    )
