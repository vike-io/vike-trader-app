"""Experiment recorder: log every run/trial to SQLite -> honest DSR trial count."""

import pytest

from vike_trader_app.analysis.recorder import ExperimentRecorder
from vike_trader_app.core.engine import Result
from vike_trader_app.core.model import Trade
from vike_trader_app.data.store import Store


def _result(final=10_120.0):
    eq = [10_000.0, 10_050.0, 9_900.0, final]
    trades = [Trade(entry_price=100, exit_price=102, size=1, pnl=2.0, fees=0.1, entry_ts=0, exit_ts=60_000)]
    return Result(trades=trades, equity_curve=eq, final_equity=final)


def test_recorder_logs_runs_and_counts_trials():
    rec = ExperimentRecorder(Store(":memory:"))
    rec.record(symbol="BTCUSDT", interval="1m", strategy="SMA", params={"fast": 5}, result=_result(), ts=1)
    rec.record(symbol="BTCUSDT", interval="1m", strategy="SMA", params={"fast": 10}, result=_result(10_200.0), ts=2)
    rec.record(symbol="ETHUSDT", interval="1m", strategy="RSI", params={"p": 14}, result=_result(), ts=3)
    assert rec.n_trials() == 3
    assert rec.n_trials(strategy="SMA") == 2  # feeds deflated-Sharpe's trial count


def test_recorder_persists_metrics_from_result():
    store = Store(":memory:")
    rec = ExperimentRecorder(store)
    rec.record(symbol="BTCUSDT", interval="1m", strategy="SMA", params={"fast": 5}, result=_result(10_120.0), ts=1)
    saved = store.list_runs()[0]
    assert saved.final_equity == pytest.approx(10_120.0)
    assert saved.trades == 1
    assert saved.params == {"fast": 5}


def test_recorder_clamps_infinite_profit_factor():
    # all-winning trades -> profit_factor is inf; must be stored as a finite REAL
    eq = [10_000.0, 10_100.0]
    win = [Trade(entry_price=100, exit_price=110, size=1, pnl=10.0, fees=0, entry_ts=0, exit_ts=1)]
    rec = ExperimentRecorder(Store(":memory:"))
    rid = rec.record(symbol="X", interval="1m", strategy="S", params={}, result=Result(win, eq, 10_100.0), ts=1)
    assert isinstance(rid, int)
