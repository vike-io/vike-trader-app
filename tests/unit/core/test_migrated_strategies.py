"""Task 14: verify SmaCross and MLStrategy work correctly.

SmaCross is run through PortfolioEngine (it's a PortfolioStrategy subclass).
MLStrategy is run through BacktestEngine (it's a SingleSymbolStrategy, used via ml.walkforward).
"""

import pytest

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.portfolio import PortfolioEngine
from vike_trader_app.ui.dialogs import SmaCross, default_strategy_factory
from vike_trader_app.ml.strategy import MLStrategy


def _series(closes, base_price=10.0):
    """Build a deterministic bar series where open==close (price alternates)."""
    bars = []
    for i, c in enumerate(closes):
        bars.append(Bar(ts=i * 60_000, open=c, high=c + 0.5, low=c - 0.5, close=c, volume=100.0))
    return bars


# ---------------------------------------------------------------------------
# SmaCross
# ---------------------------------------------------------------------------

class TestSmaCross:
    def _make_closes(self, n=80):
        """Alternating sawtooth so the fast SMA crosses the slow SMA at least once."""
        closes = []
        for i in range(n):
            # Rising for first half, falling for second half → guaranteed crossover
            closes.append(10.0 + i * 0.1 if i < n // 2 else 10.0 + (n - i) * 0.1)
        return closes

    def test_smacross_completes_and_equity_is_finite(self):
        closes = self._make_closes()
        eng = PortfolioEngine(
            {"BTC": _series(closes)},
            SmaCross(),
            fee_rate=0.0,
            cash=100_000.0,
        )
        result = eng.run()
        assert result.final_equity > 0
        assert result.final_equity != float("inf")

    def test_smacross_buys_on_fast_over_slow(self):
        """With a rising-then-falling series, SmaCross should complete at least one round-trip."""
        # Rising for 50 bars (buy signal), then falling for 50 bars (close signal) → closed trade
        closes = [10.0 + i * 0.5 for i in range(50)] + [35.0 - i * 0.5 for i in range(50)]
        eng = PortfolioEngine(
            {"BTC": _series(closes)},
            SmaCross(),
            fee_rate=0.0,
            cash=100_000.0,
        )
        result = eng.run()
        # After a full crossover + reversal, at least one closed trade must appear
        assert len(result.trades) >= 1

    def test_smacross_flat_does_nothing(self):
        """With only `slow` bars or fewer, SmaCross never signals — no trades."""
        strat = SmaCross()
        closes = [10.0] * strat.slow  # exactly slow bars, never enough to signal
        eng = PortfolioEngine(
            {"BTC": _series(closes)},
            strat,
            fee_rate=0.0,
            cash=100_000.0,
        )
        result = eng.run()
        assert len(result.trades) == 0

    def test_default_strategy_factory_returns_smacross(self):
        cls = default_strategy_factory()
        assert cls is SmaCross
        inst = cls()
        assert isinstance(inst, SmaCross)


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

        result = BacktestEngine(_series(closes), strat, fee_rate=0.0, cash=50_000.0).run()
        assert result.final_equity > 0
        assert result.final_equity != float("inf")

    def test_mlstrategy_skips_none_features(self):
        n = 20
        closes = [10.0] * n
        strat = MLStrategy()
        # All None features — should never trade
        strat.feats = [None] * n
        strat.predict = lambda _: 1.0  # would trade if features weren't None

        result = BacktestEngine(_series(closes), strat, fee_rate=0.0, cash=50_000.0).run()
        assert len(result.trades) == 0

    def test_mlstrategy_skips_when_index_beyond_feats(self):
        n = 20
        closes = [10.0] * n
        strat = MLStrategy()
        # Only 5 features — bars 5..19 are skipped by index guard
        strat.feats = [{"f": i} for i in range(5)]
        strat.predict = lambda feats: 1.0

        # Should not raise even when index > len(feats)
        result = BacktestEngine(_series(closes), strat, fee_rate=0.0, cash=50_000.0).run()
        assert result.final_equity > 0

    def test_mlstrategy_enters_on_positive_signal(self):
        n = 20
        closes = [10.0 + i * 0.1 for i in range(n)]
        strat = MLStrategy()
        # Signal: long for first 10 bars, then close
        strat.feats = [{"f": i} for i in range(n)]
        strat.predict = lambda feats: 1.0 if feats["f"] < 10 else -1.0

        result = BacktestEngine(_series(closes), strat, fee_rate=0.0, cash=50_000.0).run()
        # Should have entered once (and likely exited once)
        assert len(result.trades) >= 1
