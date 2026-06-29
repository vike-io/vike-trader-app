"""Task 14 (GUI portion): verify SmaCross works on the unified Strategy API.

SmaCross lives in ui/dialogs.py which imports PySide6 — so this test belongs
in tests/gui/ (Qt-aware), NOT tests/unit/ (Qt-free CI job).

Qt-free MLStrategy tests remain in tests/unit/core/test_migrated_strategies.py.
"""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.portfolio import PortfolioEngine
from vike_trader_app.ui.dialogs import SmaCross, default_strategy_factory


def _series(closes):
    """Build a deterministic bar series where open==close."""
    bars = []
    for i, c in enumerate(closes):
        bars.append(Bar(ts=i * 60_000, open=c, high=c + 0.5, low=c - 0.5, close=c, volume=100.0))
    return bars


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
