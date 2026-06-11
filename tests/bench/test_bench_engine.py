"""Performance benchmarks for hot paths (pytest-benchmark).

These live OUTSIDE the default test gate (testpaths = ["tests/unit"]) and the CI unit/gui
matrix, so they never slow normal runs. They run only in the dedicated `bench` CI job
(`pytest tests/bench`), which records timings — a baseline for spotting regressions over time.

Targets the backtest engine via the stable `services.run_sma_backtest(closes, fast, slow)` seam
(used by the MCP tools), on deterministic synthetic price series of varying length. No network,
no Qt, no randomness (benchmark runs must be comparable across CI runs)."""

import math

import pytest

from vike_trader_app.ai import services


def _closes(n: int) -> list[float]:
    # deterministic: a slow sine trend + a short cycle, so the SMA crossover actually trades
    return [100.0 + 12.0 * math.sin(i / 90.0) + 3.0 * math.sin(i / 7.0) for i in range(n)]


@pytest.mark.parametrize("n", [5_000, 50_000])
def test_bench_sma_backtest(benchmark, n):
    closes = _closes(n)
    result = benchmark(services.run_sma_backtest, closes, 10, 30, fee_rate=0.001)
    assert isinstance(result, dict)   # sanity: the hot path still returns a report


def test_bench_sma_backtest_tight_windows(benchmark):
    # many crossovers (fast/slow close together) -> exercises the trade-execution path harder
    closes = _closes(20_000)
    result = benchmark(services.run_sma_backtest, closes, 5, 8, fee_rate=0.0005)
    assert isinstance(result, dict)
