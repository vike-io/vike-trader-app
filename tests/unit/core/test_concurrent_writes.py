import threading

from vike_trader_app.core.model import Bar
from vike_trader_app.data.parquet_source import append_series, read_series


def _bars(lo, hi):
    return [Bar(ts=i * 60_000, open=1.0 + i, high=2.0 + i, low=0.5 + i, close=1.5 + i, volume=10.0 + i)
            for i in range(lo, hi)]


def test_concurrent_same_series_appends_no_corruption(tmp_path):
    root = str(tmp_path)
    # 8 threads each append a DISJOINT range of bars to the SAME (symbol, interval)
    errors = []
    chunks = [(_bars(i * 25, i * 25 + 25)) for i in range(8)]

    def worker(bars):
        try:
            append_series(bars, root, "AAA", "1m")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(c,)) for c in chunks]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors
    got = read_series(root, "AAA", "1m")
    assert [b.ts for b in got] == [i * 60_000 for i in range(200)]   # all 200 bars present, sorted, deduped — no lost write


def test_concurrent_different_series_no_crossblock(tmp_path):
    root = str(tmp_path)
    errors = []

    def worker(sym):
        try:
            for _ in range(20):
                append_series(_bars(0, 50), root, sym, "1m")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(s,)) for s in ("AAA", "BBB", "CCC", "DDD")]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors
    for s in ("AAA", "BBB", "CCC", "DDD"):
        assert len(read_series(root, s, "1m")) == 50
