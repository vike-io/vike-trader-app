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


def test_run_sma_backtest_returns_full_metrics():
    closes = [b.close for b in _synth_bars(80)]
    out = run_sma_backtest(closes, fast=5, slow=20, fee_rate=0.0)
    for key in ("total_return", "sharpe", "sortino", "calmar", "max_drawdown",
                "win_rate", "profit_factor", "n_trades", "final_equity"):
        assert key in out
    assert isinstance(out["n_trades"], int)
    assert isinstance(out["final_equity"], float)
    assert out["params"] == {"fast": 5, "slow": 20}


def test_run_sma_backtest_no_trades_on_flat_series():
    closes = [100.0] * 50
    out = run_sma_backtest(closes, fast=5, slow=20)
    assert out["n_trades"] == 0
    assert out["total_return"] == pytest.approx(0.0)


def test_optimize_sma_ranks_and_limits():
    closes = [b.close for b in _synth_bars(120)]
    out = optimize_sma(closes, fasts=[5, 10], slows=[20, 40], fee_rate=0.0, top_n=3)
    assert out["n_combos"] == 4
    assert len(out["top"]) == 3
    rets = [r["total_return"] for r in out["top"]]
    assert rets == sorted(rets, reverse=True)
    assert set(out["top"][0]["params"]) == {"fast", "slow"}


def test_overfit_check_returns_verdict():
    out = overfit_check(observed_sr=1.5, trial_sharpes=[1.4, 1.45, 1.55, 1.3, 1.2], n_obs=500)
    assert 0.0 <= out["deflated_sharpe"] <= 1.0
    assert out["verdict"]["level"] in {"Low", "Medium", "High"}
    assert isinstance(out["verdict"]["reasons"], list)
    assert out["pbo"] == 0.0  # no matrix supplied -> no fabricated PBO


def test_overfit_check_with_pbo_matrix():
    matrix = [[0.1, 0.2, -0.1], [0.0, 0.1, 0.05], [-0.1, 0.0, 0.1], [0.2, -0.1, 0.0],
              [0.1, 0.1, 0.1], [-0.2, 0.0, 0.1], [0.05, 0.2, -0.05], [0.1, -0.1, 0.2]]
    out = overfit_check(observed_sr=1.0, trial_sharpes=[0.9, 1.0, 1.1],
                        n_obs=8, pbo_matrix=matrix, n_splits=4)
    assert 0.0 <= out["pbo"] <= 1.0
    assert out["verdict"]["level"] in {"Low", "Medium", "High"}
