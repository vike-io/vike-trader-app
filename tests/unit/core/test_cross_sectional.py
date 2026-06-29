"""Cross-sectional top-k rotation: rank the universe each rebalance, hold the best k."""

from datetime import datetime, timezone

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.multi_symbol_engine import CrossSectionalStrategy, MultiSymbolEngine, PortfolioStrategy
from vike_trader_app.core.strategy import Strategy


def _series(opens):
    return [Bar(ts=i * 60_000, open=o, high=o + 1, low=o - 1, close=o, volume=1.0) for i, o in enumerate(opens)]


class Momentum(CrossSectionalStrategy):
    k = 2
    rebalance_every = 1
    lookback = 3

    def score(self, symbol, history):
        if len(history) <= self.lookback:
            return None
        return history[-1] / history[-1 - self.lookback] - 1.0  # trailing return


def test_top_k_holds_strongest_drops_weakest():
    # A rises fastest, B medium, C falls -> top-2 momentum = {A, B}; C never held
    bars = {
        "A": _series([100, 105, 112, 121, 132, 145, 160, 177]),
        "B": _series([100, 101, 103, 105, 108, 111, 114, 118]),
        "C": _series([100, 99, 98, 97, 96, 95, 94, 93]),
    }
    eng = MultiSymbolEngine(bars, Momentum(), cash=100_000.0)
    eng.run()
    assert eng.position_of("A").size > 0
    assert eng.position_of("B").size > 0
    assert eng.position_of("C").size == pytest.approx(0.0)  # rotated out


class _RebalanceEvery3(CrossSectionalStrategy):
    k = 1
    rebalance_every = 3
    lookback = 1

    def __init__(self):
        super().__init__()
        self.rebalanced_on = []

    def score(self, symbol, history):
        if len(history) <= self.lookback:
            return None
        self.rebalanced_on.append(self.index)
        return history[-1]

    def weights(self, winners):
        return {winners[0]: 1.0}


def test_rebalance_cadence_only_fires_on_schedule():
    bars = {"A": _series([100] * 9), "B": _series([101] * 9)}
    strat = _RebalanceEvery3()
    MultiSymbolEngine(bars, strat, cash=10_000.0).run()
    # score() only runs on indices divisible by rebalance_every (0,3,6) and after warmup
    assert set(strat.rebalanced_on) <= {0, 3, 6}


# ---------------------------------------------------------------------------
# Calendar rebalancing (rebalance_on="monthly")
# ---------------------------------------------------------------------------

def _ts_ms(year: int, month: int, day: int) -> int:
    """Epoch-ms for midnight UTC on the given date."""
    dt = datetime(year, month, day, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _daily_bars(symbol: str, dates: list[tuple[int, int, int]], price_fn) -> list[Bar]:
    """Build daily bars for ``symbol`` using ``price_fn(i)`` for the close."""
    bars = []
    for i, (y, m, d) in enumerate(dates):
        ts = _ts_ms(y, m, d)
        p = price_fn(i)
        bars.append(Bar(ts=ts, open=p, high=p + 1, low=p - 1, close=p, volume=1.0))
    return bars


# Dates spanning Jan, Feb, Mar 2024 — ~10 trading days per month.
_DATES = [
    # Jan 2024
    (2024, 1, 2), (2024, 1, 5), (2024, 1, 9), (2024, 1, 12), (2024, 1, 16),
    (2024, 1, 19), (2024, 1, 23), (2024, 1, 26), (2024, 1, 30),
    # Feb 2024
    (2024, 2, 2), (2024, 2, 6), (2024, 2, 9), (2024, 2, 13), (2024, 2, 16),
    (2024, 2, 20), (2024, 2, 23), (2024, 2, 27),
    # Mar 2024
    (2024, 3, 1), (2024, 3, 5), (2024, 3, 8), (2024, 3, 12), (2024, 3, 15),
    (2024, 3, 19), (2024, 3, 22), (2024, 3, 26),
]

_N = len(_DATES)  # total bars


class _MonthlyRotation(CrossSectionalStrategy):
    """k=1 strategy that scores by last close; rebalances on monthly boundaries."""

    k = 1
    rebalance_on = "monthly"

    def __init__(self):
        super().__init__()
        self.rebalance_calls: list[int] = []  # bar indices where rebalance() is called
        self.score_call_count = 0

    def score(self, symbol: str, history: list[float]):
        self.score_call_count += 1
        return history[-1]

    def rebalance(self, weights: dict) -> None:  # type: ignore[override]
        self.rebalance_calls.append(self.index)
        super().rebalance(weights)


def test_monthly_rebalance_fires_exactly_once_per_month():
    """With 3 calendar months of daily bars, rebalance() should fire exactly 3 times."""
    bars = {
        "A": _daily_bars("A", _DATES, lambda i: 100 + i),   # steadily rising
        "B": _daily_bars("B", _DATES, lambda i: 50 + i),    # rising but lower
    }
    strat = _MonthlyRotation()
    MultiSymbolEngine(bars, strat, cash=10_000.0).run()

    # Exactly 3 month boundaries crossed (Jan, Feb, Mar first bar each month)
    assert len(strat.rebalance_calls) == 3


def test_monthly_rebalance_triggers_at_month_start_bars():
    """The rebalance should fire on the first bar of each new month."""
    bars = {
        "A": _daily_bars("A", _DATES, lambda i: 100 + i),
        "B": _daily_bars("B", _DATES, lambda i: 50 + i),
    }
    strat = _MonthlyRotation()
    MultiSymbolEngine(bars, strat, cash=10_000.0).run()

    # Identify bar indices that are first occurrences of a new month
    from vike_trader_app.analysis.periods import period_key
    seen_months: set[str] = set()
    expected_trigger_indices: list[int] = []
    for idx, date in enumerate(_DATES):
        ts = _ts_ms(*date)
        key = period_key(ts, "monthly")
        if key not in seen_months:
            seen_months.add(key)
            expected_trigger_indices.append(idx)

    assert strat.rebalance_calls == expected_trigger_indices


def test_monthly_rebalance_history_accumulated_on_all_bars():
    """Price history must be accumulated every bar, not just on rebalance bars."""
    bars = {
        "A": _daily_bars("A", _DATES, lambda i: 100 + i),
        "B": _daily_bars("B", _DATES, lambda i: 50 + i),
    }
    strat = _MonthlyRotation()
    eng = MultiSymbolEngine(bars, strat, cash=10_000.0)
    eng.run()

    # History length for each symbol should equal total number of bars
    assert len(strat._hist["A"]) == _N
    assert len(strat._hist["B"]) == _N


def test_rebalance_on_none_reproduces_bar_count_behavior():
    """rebalance_on=None (default) must produce the same result as rebalance_every=1."""

    class _BarCount(CrossSectionalStrategy):
        k = 1
        rebalance_every = 1
        rebalance_on = None

        def score(self, symbol, history):
            return history[-1]

    class _BarCountAlt(CrossSectionalStrategy):
        """Same but using the default CrossSectionalStrategy defaults."""
        k = 1

        def score(self, symbol, history):
            return history[-1]

    bars_a = {
        "A": _series([100, 105, 102, 108, 115]),
        "B": _series([99, 104, 101, 107, 114]),
    }
    # Make identical bar dicts for the two runs
    bars_b = {
        "A": _series([100, 105, 102, 108, 115]),
        "B": _series([99, 104, 101, 107, 114]),
    }

    strat_none = _BarCount()
    result_none = MultiSymbolEngine(bars_a, strat_none, cash=10_000.0).run()

    strat_default = _BarCountAlt()
    result_default = MultiSymbolEngine(bars_b, strat_default, cash=10_000.0).run()

    # Both strategies should produce the same final equity
    assert result_none.final_equity == pytest.approx(result_default.final_equity)


def test_rebalance_every_still_gates():
    """rebalance_every=3 (no rebalance_on) must rebalance only on bars 0, 3, 6 — identical
    behavior to the pre-Schedule inline gate."""

    class _Every3(CrossSectionalStrategy):
        k = 1
        rebalance_every = 3
        rebalance_on = None
        lookback = 1

        def __init__(self):
            super().__init__()
            self.rebalance_calls: list[int] = []  # bar indices where _rebalance fires

        def score(self, symbol, history):
            if len(history) <= self.lookback:
                return None
            return history[-1]

        def rebalance(self, weights: dict) -> None:  # type: ignore[override]
            self.rebalance_calls.append(self.index)
            super().rebalance(weights)

        def weights(self, winners):
            return {winners[0]: 1.0}

    # 9 bars -> rebalance eligible at indices 0, 3, 6; index 0 skipped (warmup: score returns None)
    bars = {
        "A": _series([100] * 9),
        "B": _series([101] * 9),
    }
    strat = _Every3()
    MultiSymbolEngine(bars, strat, cash=10_000.0).run()

    # rebalance() fires only on bars 3 and 6 (bar 0 is skipped by warmup — score returns None for
    # len(history)==1 <= lookback==1); bar-count gate of 3 is preserved through Schedule.
    assert all(idx % 3 == 0 for idx in strat.rebalance_calls), (
        f"rebalance fired on non-multiple-of-3 bars: {strat.rebalance_calls}"
    )
    # At least bars 3 and 6 must fire (proving the gate works, not just that it never fires)
    assert 3 in strat.rebalance_calls
    assert 6 in strat.rebalance_calls


def test_cross_sectional_reparented_to_strategy():
    """CrossSectionalStrategy must subclass unified Strategy, not the deprecated PortfolioStrategy."""
    assert issubclass(CrossSectionalStrategy, Strategy)
    assert not issubclass(CrossSectionalStrategy, PortfolioStrategy)
