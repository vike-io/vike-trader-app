"""SQLite run-history store: save / list / delete / clear, params round-trip, persistence."""

from vike_trader_app.core.model import Bar
from vike_trader_app.data.store import RunRecord, Store


def _rec(**kw):
    base = {
        "ts": 1000,
        "symbol": "BTCUSDT",
        "interval": "1m",
        "strategy": "SmaCross",
        "start_ts": 0,
        "end_ts": 600,
        "n_bars": 11,
        "net_return": 0.05,
        "final_equity": 10500.0,
        "trades": 3,
        "win_rate": 0.66,
        "profit_factor": 2.1,
        "max_drawdown": 0.08,
        "sharpe": 1.4,
        "params": {"fast": 10, "slow": 30},
    }
    base.update(kw)
    return RunRecord(**base)


def test_save_returns_id_and_lists_it():
    s = Store(":memory:")
    rid = s.save_run(_rec())
    assert isinstance(rid, int)
    runs = s.list_runs()
    assert len(runs) == 1
    assert runs[0].symbol == "BTCUSDT"
    assert runs[0].id == rid


def test_params_dict_roundtrips():
    s = Store(":memory:")
    s.save_run(_rec(params={"fast": 5, "slow": 20}))
    assert s.list_runs()[0].params == {"fast": 5, "slow": 20}


def test_float_fields_roundtrip():
    s = Store(":memory:")
    s.save_run(_rec(sharpe=2.34, net_return=-0.12))
    r = s.list_runs()[0]
    assert r.sharpe == 2.34
    assert r.net_return == -0.12


def test_newest_first():
    s = Store(":memory:")
    s.save_run(_rec(ts=1000, symbol="AAA"))
    s.save_run(_rec(ts=2000, symbol="BBB"))
    assert [r.symbol for r in s.list_runs()] == ["BBB", "AAA"]


def test_list_respects_limit():
    s = Store(":memory:")
    for i in range(5):
        s.save_run(_rec(ts=i))
    assert len(s.list_runs(limit=3)) == 3


def test_delete_removes_one():
    s = Store(":memory:")
    rid = s.save_run(_rec())
    s.save_run(_rec(ts=2000))
    s.delete_run(rid)
    runs = s.list_runs()
    assert len(runs) == 1
    assert runs[0].id != rid


def test_clear_empties():
    s = Store(":memory:")
    s.save_run(_rec())
    s.save_run(_rec())
    s.clear()
    assert s.list_runs() == []


def test_file_backed_persists_across_open(tmp_path):
    path = str(tmp_path / "db" / "v.sqlite")  # nested dir must be created
    s = Store(path)
    s.save_run(_rec())
    s.close()
    s2 = Store(path)
    assert len(s2.list_runs()) == 1
    assert s2.list_runs()[0].symbol == "BTCUSDT"


# --- forward (paper) run persistence -------------------------------------------

def _fbar(ts, c=100.0, funding=None):
    return Bar(ts=ts, open=c, high=c + 1, low=c - 1, close=c, volume=2.0, funding=funding)


def test_create_forward_run_lists_it():
    s = Store(":memory:")
    rid = s.create_forward_run(
        symbol="BTCUSDT", interval="1m", strategy="SmaCross",
        cash=10_000.0, fee_rate=0.001, params={"fast": 5}, created_ts=1000,
    )
    runs = s.list_forward_runs()
    assert len(runs) == 1
    assert runs[0].id == rid
    assert runs[0].symbol == "BTCUSDT"
    assert runs[0].params == {"fast": 5}
    assert runs[0].status == "running"


def test_forward_bars_roundtrip_ascending_with_funding():
    s = Store(":memory:")
    rid = s.create_forward_run(
        symbol="BTCUSDT", interval="1m", strategy="S", cash=1.0, fee_rate=0.0,
        params={}, created_ts=0,
    )
    s.append_forward_bar(rid, _fbar(60_000, funding=0.01))
    s.append_forward_bar(rid, _fbar(0))  # out of order on purpose
    bars = s.forward_bars(rid)
    assert [b.ts for b in bars] == [0, 60_000]
    assert bars[1].funding == 0.01
    assert bars[0].funding is None


def test_append_forward_bar_dedupes_by_ts():
    s = Store(":memory:")
    rid = s.create_forward_run(
        symbol="X", interval="1m", strategy="S", cash=1.0, fee_rate=0.0, params={}, created_ts=0,
    )
    s.append_forward_bar(rid, _fbar(0, c=100.0))
    s.append_forward_bar(rid, _fbar(0, c=999.0))  # same ts -> replaces, no duplicate row
    bars = s.forward_bars(rid)
    assert len(bars) == 1
    assert bars[0].close == 999.0


def test_set_forward_status():
    s = Store(":memory:")
    rid = s.create_forward_run(
        symbol="X", interval="1m", strategy="S", cash=1.0, fee_rate=0.0, params={}, created_ts=0,
    )
    s.set_forward_status(rid, "stopped")
    assert s.list_forward_runs()[0].status == "stopped"


def test_forward_run_persists_across_reopen(tmp_path):
    path = str(tmp_path / "db" / "v.sqlite")
    s = Store(path)
    rid = s.create_forward_run(
        symbol="BTCUSDT", interval="1m", strategy="S", cash=10_000.0, fee_rate=0.0,
        params={}, created_ts=0,
    )
    s.append_forward_bar(rid, _fbar(0))
    s.append_forward_bar(rid, _fbar(60_000))
    s.close()
    s2 = Store(path)  # resume: state survives closing the app
    assert len(s2.list_forward_runs()) == 1
    assert [b.ts for b in s2.forward_bars(rid)] == [0, 60_000]
