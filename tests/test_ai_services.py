"""AI service layer: pure JSON-friendly wrappers over the engine/optimizer/overfit code."""

import pytest

from vike_trader_app.ai.services import (
    bars_to_data, fetch_ohlcv, run_sma_backtest, optimize_sma, overfit_check,
)
from vike_trader_app.core.model import Bar


def _synth_bars(n=60):
    bars = []
    price = 100.0
    for i in range(n):
        price = price + 1.0 + (1.5 if i % 5 == 0 else -0.5)
        bars.append(Bar(ts=i * 3_600_000, open=price - 0.5, high=price + 1.0,
                        low=price - 1.0, close=price, volume=1.0))
    return bars


def test_bars_to_data_shapes():
    bars = _synth_bars(5)
    d = bars_to_data(bars)
    assert set(d) == {"open", "high", "low", "close", "ts", "funding"}
    assert d["close"] == [b.close for b in bars]
    assert d["funding"] == [0.0] * 5


def test_fetch_ohlcv_uses_injected_fetcher_and_summarizes():
    bars = _synth_bars(10)
    called = {}

    def fake_fetcher(symbol, interval, start_ms, end_ms, progress=None):
        called["args"] = (symbol, interval, start_ms, end_ms)
        return bars

    out = fetch_ohlcv("BTCUSDT", "1h", 0, 10 * 3_600_000, source="binance", fetcher=fake_fetcher)
    assert called["args"][0] == "BTCUSDT"
    assert out["symbol"] == "BTCUSDT"
    assert out["interval"] == "1h"
    assert out["n_bars"] == 10
    assert out["first_ts"] == bars[0].ts
    assert out["last_ts"] == bars[-1].ts
    assert out["closes"] == [b.close for b in bars]


# ---------------------------------------------------------------------------
# Task 2: run_sma_backtest
# ---------------------------------------------------------------------------

def test_run_sma_backtest_returns_expected_keys():
    bars = _synth_bars(60)
    result = run_sma_backtest(bars, fast=5, slow=20)
    expected_keys = {
        "fast", "slow", "n_trades", "total_return", "win_rate",
        "max_drawdown", "profit_factor", "sharpe", "sortino", "calmar", "omega",
    }
    assert set(result) == expected_keys
    assert result["fast"] == 5
    assert result["slow"] == 20


def test_run_sma_backtest_flat_series_no_trades():
    """Constant closes must produce zero trades — no crossover ever fires."""
    bars = [Bar(ts=i * 3_600_000, open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0)
            for i in range(60)]
    result = run_sma_backtest(bars, fast=5, slow=20)
    assert result["n_trades"] == 0


# ---------------------------------------------------------------------------
# Task 3: optimize_sma
# ---------------------------------------------------------------------------

def test_optimize_sma_returns_ranked_results():
    bars = _synth_bars(60)
    result = optimize_sma(bars, fasts=[3, 5, 10], slows=[15, 20, 30])
    assert "best" in result
    assert "top" in result
    assert "n_combos" in result
    # All valid combos have fast < slow
    assert result["n_combos"] > 0
    for entry in result["top"]:
        assert entry["params"]["fast"] < entry["params"]["slow"]
    # top is ranked best-first (descending total_return)
    returns = [r["total_return"] for r in result["top"]]
    assert returns == sorted(returns, reverse=True)


# ---------------------------------------------------------------------------
# Task 4: overfit_check
# ---------------------------------------------------------------------------

def test_overfit_check_returns_expected_keys_and_ranges():
    # Build synthetic trial results: 9 trials with varying sharpe
    trial_results = [{"sharpe": 0.1 * i} for i in range(1, 10)]
    result = overfit_check(trial_results, n_obs=60)
    assert set(result) >= {"deflated_sr", "pbo", "verdict_level", "verdict_reasons"}
    assert 0.0 <= result["deflated_sr"] <= 1.0
    assert 0.0 <= result["pbo"] <= 1.0
    assert result["verdict_level"] in ("Low", "Medium", "High")
    assert isinstance(result["verdict_reasons"], list)


def test_overfit_check_high_trial_count_yields_lower_dsr():
    """More trials should reduce the deflated Sharpe (harder to be significant)."""
    few_trials = [{"sharpe": 1.5}] * 2
    many_trials = [{"sharpe": s} for s in [1.5] + [0.1 * i for i in range(20)]]
    result_few = overfit_check(few_trials, n_obs=100)
    result_many = overfit_check(many_trials, n_obs=100)
    # With many more trials, deflated SR should be lower (more competition)
    assert result_few["deflated_sr"] >= result_many["deflated_sr"]
