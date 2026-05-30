"""SQLite run-history store: save / list / delete / clear, params round-trip, persistence."""

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
