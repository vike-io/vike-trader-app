"""Reproducible result-bundle tests."""

from vike_trader_app.analysis.persistence import data_hash
from vike_trader_app.core.model import Bar


def _bars(opens):
    return [Bar(ts=i * 60_000, open=o, high=o + 1, low=o - 1, close=o, volume=1.0) for i, o in enumerate(opens)]


def test_data_hash_is_deterministic_and_window_sensitive():
    a = data_hash(_bars([100, 101, 102]))
    b = data_hash(_bars([100, 101, 102]))
    c = data_hash(_bars([100, 101, 103]))
    assert a == b              # same data -> same hash
    assert a != c              # different data -> different hash
    assert len(a) == 64        # sha-256 hex


import json as _json  # noqa: E402

from vike_trader_app.analysis.persistence import save_bundle  # noqa: E402
from vike_trader_app.analysis.tearsheet import write_tearsheet_html  # noqa: E402
from vike_trader_app.core.engine import BacktestEngine, Result  # noqa: E402
from vike_trader_app.core.model import Trade  # noqa: E402
from vike_trader_app.core.strategy import Strategy  # noqa: E402


def _result():
    eq = [10_000.0, 10_010.0, 10_005.0, 10_020.0]
    trades = [Trade(entry_price=100, exit_price=102, size=1, pnl=2.0, fees=0.1, entry_ts=0, exit_ts=120_000)]
    return Result(trades=trades, equity_curve=eq, final_equity=10_020.0)


def test_save_bundle_writes_all_artifacts(tmp_path):
    bars = _bars([100, 101, 102, 103])
    out = save_bundle(
        tmp_path / "run1",
        result=_result(),
        strategy_source="class S: pass\n",
        params={"fast": 10, "slow": 20},
        config={"symbol": "BTCUSDT", "interval": "1m", "fee_rate": 0.001, "cash": 10_000.0},
        bars=bars,
        seed=42,
    )
    assert (out / "strategy.py").read_text() == "class S: pass\n"
    assert _json.loads((out / "params.json").read_text()) == {"fast": 10, "slow": 20}
    cfg = _json.loads((out / "config.json").read_text())
    assert cfg["seed"] == 42
    assert cfg["data_hash"] == data_hash(bars)
    assert cfg["n_bars"] == 4 and cfg["start_ts"] == 0 and cfg["end_ts"] == 180_000
    m = _json.loads((out / "metrics.json").read_text())
    assert m["final_equity"] == 10_020.0 and m["n_trades"] == 1
    rows = (out / "trades.csv").read_text().splitlines()
    assert rows[0] == "entry_price,exit_price,size,pnl,fees,entry_ts,exit_ts"
    assert rows[1].startswith("100")


class _Buy(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)


def test_bundle_and_tearsheet_from_real_run(tmp_path):
    bars = _bars([100, 110, 120, 130])
    result = BacktestEngine(bars, _Buy(), fee_rate=0.001).run()
    out = save_bundle(tmp_path / "r", result=result, params={}, config={"symbol": "X"}, bars=bars, seed=1)
    report = write_tearsheet_html(out / "report.html", result, title="X 1m")
    assert (out / "metrics.json").exists()
    assert report.exists() and "<svg" in report.read_text()
