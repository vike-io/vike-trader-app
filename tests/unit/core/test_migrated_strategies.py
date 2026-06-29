"""Task 14 (unit portion): verify MLStrategy works on the unified Strategy API.

SmaCross lives in ui/dialogs.py which imports PySide6 — those tests have been
moved to tests/gui/panels/test_migrated_smacross.py (Qt-aware job).
This file tests only Qt-free MLStrategy.
"""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.multi_symbol_engine import MultiSymbolEngine
from vike_trader_app.ml.strategy import MLStrategy


def _series(closes, base_price=10.0):
    """Build a deterministic bar series where open==close (price alternates)."""
    bars = []
    for i, c in enumerate(closes):
        bars.append(Bar(ts=i * 60_000, open=c, high=c + 0.5, low=c - 0.5, close=c, volume=100.0))
    return bars


# ---------------------------------------------------------------------------
# MLStrategy
# ---------------------------------------------------------------------------

class TestMLStrategy:
    def test_mlstrategy_completes_and_equity_is_finite(self):
        n = 20
        closes = [10.0 + i * 0.1 for i in range(n)]
        strat = MLStrategy()
        # Inject features and a predictor that goes long on first 10 bars
        strat.feats = [{"f": i} for i in range(n)]
        strat.predict = lambda feats: 1.0 if feats["f"] < 10 else -1.0

        eng = MultiSymbolEngine(
            {"ETH": _series(closes)},
            strat,
            fee_rate=0.0,
            cash=50_000.0,
        )
        result = eng.run()
        assert result.final_equity > 0
        assert result.final_equity != float("inf")

    def test_mlstrategy_skips_none_features(self):
        n = 20
        closes = [10.0] * n
        strat = MLStrategy()
        # All None features — should never trade
        strat.feats = [None] * n
        strat.predict = lambda _: 1.0  # would trade if features weren't None

        eng = MultiSymbolEngine(
            {"ETH": _series(closes)},
            strat,
            fee_rate=0.0,
            cash=50_000.0,
        )
        result = eng.run()
        assert len(result.trades) == 0

    def test_mlstrategy_skips_when_index_beyond_feats(self):
        n = 20
        closes = [10.0] * n
        strat = MLStrategy()
        # Only 5 features — bars 5..19 are skipped by index guard
        strat.feats = [{"f": i} for i in range(5)]
        strat.predict = lambda feats: 1.0

        eng = MultiSymbolEngine(
            {"ETH": _series(closes)},
            strat,
            fee_rate=0.0,
            cash=50_000.0,
        )
        # Should not raise even when index > len(feats)
        result = eng.run()
        assert result.final_equity > 0

    def test_mlstrategy_enters_on_positive_signal(self):
        n = 20
        closes = [10.0 + i * 0.1 for i in range(n)]
        strat = MLStrategy()
        # Signal: long for first 10 bars, then close
        strat.feats = [{"f": i} for i in range(n)]
        strat.predict = lambda feats: 1.0 if feats["f"] < 10 else -1.0

        eng = MultiSymbolEngine(
            {"ETH": _series(closes)},
            strat,
            fee_rate=0.0,
            cash=50_000.0,
        )
        result = eng.run()
        # Should have entered once (and likely exited once)
        assert len(result.trades) >= 1
