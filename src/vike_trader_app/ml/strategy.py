"""MLStrategy — feeds per-bar features to a trained predictor and trades its signal.

Model-agnostic (pybroker-style): the engine knows nothing about the model. A predictor
callable ``features -> signal`` is injected (trained elsewhere, e.g. inside a
walk-forward window). Default policy: go long while the signal is positive, flat otherwise.
"""

from ..core.compat_strategy import SingleSymbolStrategy


class MLStrategy(SingleSymbolStrategy):
    """Strategy driven by a model's per-bar prediction.

    Set ``feats`` (per-bar feature list, aligned to the run's bars) and ``predict``
    (callable ``features -> float``) before running. ``None`` features are skipped.
    """

    def __init__(self) -> None:
        super().__init__()
        self.feats: list = []
        self.predict = lambda features: 0.0  # noqa: ARG005 - replaced before run

    def on_bar(self, bar) -> None:
        if self.index >= len(self.feats):
            return
        features = self.feats[self.index]
        if features is None:
            return
        signal = self.predict(features)
        if signal > 0 and self.position.size == 0:
            self.buy(1.0)
        elif signal <= 0 and self.position.size > 0:
            self.close()
