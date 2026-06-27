import struct
from vike_trader_app.data import dukascopy_source as dk
from vike_trader_app.data import tick_store

_REC = struct.Struct(">3i2f")


def _hour_blob(ms_offsets_points):
    # ask/bid in points (divisor 1e5 for EURUSD); raw bytes are LZMA-compressed in prod,
    # but fetch_ticks_range decompresses — so inject via fetch_hour returning the COMPRESSED form.
    import lzma
    raw = b"".join(_REC.pack(ms, ask, bid, 1.0, 1.0) for ms, ask, bid in ms_offsets_points)
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


def test_cache_quote_ticks_persists_to_store(tmp_path):
    # one tick at hour 0, offset 0: ask=120000 pts, bid=110000 pts -> 1.20 / 1.10
    blob = _hour_blob([(0, 120000, 110000)])
    captured = {}

    def fake_fetch_hour(symbol, hour_start_ms):
        captured["called"] = True
        return blob if hour_start_ms == 0 else None

    n = dk.cache_quote_ticks_range("EURUSD", 0, 3_600_000, str(tmp_path), fetch_hour=fake_fetch_hour)
    assert n == 1
    got = tick_store.read_quotes(str(tmp_path), "EURUSD", 0, 3_600_000)
    assert len(got) == 1
    assert round(got[0].ask, 5) == 1.20000 and round(got[0].bid, 5) == 1.10000
