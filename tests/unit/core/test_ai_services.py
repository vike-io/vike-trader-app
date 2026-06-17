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


def test_run_sma_backtest_accepts_string_closes_forms():
    """An MCP/LLM client may pass `closes` as a real list, a JSON-array string, or a comma/space
    string. All three must yield the SAME result — the old list(map(float, closes)) iterated a
    string char-by-char and died on float(','). Guards the reported MCP parse bug."""
    import json

    closes = [b.close for b in _synth_bars(80)]
    ref = run_sma_backtest(closes, fast=5, slow=20)
    as_json = run_sma_backtest(json.dumps(closes), fast=5, slow=20)
    as_csv = run_sma_backtest(",".join(str(c) for c in closes), fast=5, slow=20)
    as_space = run_sma_backtest(" ".join(str(c) for c in closes), fast=5, slow=20)
    for other in (as_json, as_csv, as_space):
        assert other["final_equity"] == ref["final_equity"]
        assert other["n_trades"] == ref["n_trades"]


def test_run_sma_backtest_garbage_closes_raises_clear_error():
    """Unparseable `closes` must raise a clear ValueError (never iterate a string char-by-char)."""
    with pytest.raises(ValueError, match="must be a list of numbers"):
        run_sma_backtest("not numbers", fast=5, slow=20)


def test_optimize_sma_accepts_string_closes():
    import json

    closes = [b.close for b in _synth_bars(120)]
    ref = optimize_sma(closes, fasts=[5, 10], slows=[20, 40], top_n=3)
    via_str = optimize_sma(json.dumps(closes), fasts=[5, 10], slows=[20, 40], top_n=3)
    assert via_str["n_combos"] == ref["n_combos"]
    assert [r["total_return"] for r in via_str["top"]] == [r["total_return"] for r in ref["top"]]


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


def test_list_indicators_service():
    from vike_trader_app.ai.services import list_indicators

    out = list_indicators()
    assert out["n"] >= 79
    names = {ind["name"] for ind in out["indicators"]}
    assert {"rsi", "macd", "sma", "supertrend"} & names == {"rsi", "macd", "sma"} or {"rsi", "macd", "sma"} <= names
    # each entry carries describe() metadata
    rsi = next(ind for ind in out["indicators"] if ind["name"] == "rsi")
    assert rsi["category"] == "momentum" and rsi["inputs"] == ["close"]
    assert any(p["name"] == "period" for p in rsi["params"])


def test_compute_indicator_service_single_and_multi():
    from vike_trader_app.ai.services import compute_indicator

    n = 60
    closes = [100.0 + (i % 7) for i in range(n)]
    ohlcv = {"open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
             "close": closes, "volume": [10.0] * n}
    # single-line
    r = compute_indicator("rsi", ohlcv, {"period": 14})
    assert r["name"] == "rsi" and "rsi" in r["outputs"] and len(r["outputs"]["rsi"]) == n
    # multi-line maps each declared output name
    m = compute_indicator("macd", ohlcv)
    assert set(m["outputs"]) == {"macd", "signal", "hist"}
    assert all(len(v) == n for v in m["outputs"].values())
