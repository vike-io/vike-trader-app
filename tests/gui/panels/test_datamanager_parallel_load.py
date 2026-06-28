"""Tests for parallel multi-symbol dataset load in DataManagerTab._on_test_dataset_req.

Mirrors the fixture/setup pattern from test_datamanager_gui.py (offscreen QApplication,
DataManagerTab with tmp_path root, monkeypatch _load_symbol_bars via the module-level get_bars).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.data.datasets import DataSet  # noqa: E402
from vike_trader_app.ui.datamanager import DataManagerTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bar(sym_offset: int = 0) -> Bar:
    return Bar(ts=1_700_000_000_000 + sym_offset, open=1, high=1, low=1, close=1, volume=1.0)


# ---------------------------------------------------------------------------
# Test 1: parallel load produces the same dict as a serial map would
# ---------------------------------------------------------------------------

def test_dataset_req_loads_all_symbols_parallel(app, tmp_path, monkeypatch):
    """_on_test_dataset_req (parallel) emits the same {symbol: bars} as a serial map over _load_symbol_bars."""
    import vike_trader_app.ui.datamanager as dm

    # One distinct bar list per symbol so we can confirm the mapping is correct.
    sym_bars = {
        "BTCUSDT": [_bar(0)],
        "ETHUSDT": [_bar(1)],
        "SOLUSDT": [_bar(2)],
    }

    def fake_get_bars(symbol, interval, start, end, root=None, fetcher=None, progress=None):  # noqa: ARG001
        return sym_bars[symbol]

    monkeypatch.setattr(dm, "get_bars", fake_get_bars)

    tab = dm.DataManagerTab(root=str(tmp_path), config_root=str(tmp_path))

    reports = []
    tab.test_dataset_requested.connect(lambda ds, bars_by_symbol: reports.append((ds, bars_by_symbol)))

    ds = DataSet("TestDS", list(sym_bars.keys()), interval="1m")
    tab.panel.test_dataset_requested.emit(ds)

    assert len(reports) == 1
    _, emitted = reports[0]

    # Every symbol is present and maps to the correct bar list.
    assert set(emitted.keys()) == set(sym_bars.keys())
    for sym, expected_bars in sym_bars.items():
        assert emitted[sym] == expected_bars, f"{sym}: bar mismatch"


# ---------------------------------------------------------------------------
# Test 2: a failing symbol is isolated — others still present, no exception escapes
# ---------------------------------------------------------------------------

def test_dataset_req_isolates_failing_symbol(app, tmp_path, monkeypatch):
    """When _load_symbol_bars raises for one symbol, that symbol is absent from the emitted dict
    while all other symbols are still present — and no exception escapes to the caller."""
    import vike_trader_app.ui.datamanager as dm

    good_bar = _bar(99)
    bad_sym = "BADUSDT"

    def fake_get_bars(symbol, interval, start, end, root=None, fetcher=None, progress=None):  # noqa: ARG001
        if symbol == bad_sym:
            raise RuntimeError("simulated fetch failure")
        return [good_bar]

    monkeypatch.setattr(dm, "get_bars", fake_get_bars)

    tab = dm.DataManagerTab(root=str(tmp_path), config_root=str(tmp_path))

    reports = []
    tab.test_dataset_requested.connect(lambda ds, bars_by_symbol: reports.append((ds, bars_by_symbol)))

    symbols = ["BTCUSDT", bad_sym, "ETHUSDT"]
    ds = DataSet("ErrDS", symbols, interval="1m")

    # Must not raise.
    tab.panel.test_dataset_requested.emit(ds)

    assert len(reports) == 1, "signal must still be emitted even when one symbol fails"
    _, emitted = reports[0]

    # Failing symbol absent; good symbols present.
    assert bad_sym not in emitted
    assert "BTCUSDT" in emitted
    assert "ETHUSDT" in emitted
    assert emitted["BTCUSDT"] == [good_bar]
    assert emitted["ETHUSDT"] == [good_bar]
