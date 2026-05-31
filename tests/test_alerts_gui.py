"""Offscreen tests for the Alerts tab (reads a temp Parquet cache, reuses the screener)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.analysis.alerts import AlertRule, AlertStore  # noqa: E402
from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.data.catalog import Catalog  # noqa: E402
from vike_trader_app.data.parquet_source import write_bars_parquet  # noqa: E402
from vike_trader_app.ui.alerts import AlertsTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_alerts_add_and_persist(app, tmp_path):
    path = str(tmp_path / "a.json")
    tab = AlertsTab(store=AlertStore(path))
    tab._symbol.setCurrentText("EURUSD")
    tab._dir.setCurrentText("long")
    tab._add()
    assert tab._table.rowCount() == 1
    assert AlertStore(path).rules()[0].symbol == "EURUSD"   # persisted


def test_alerts_check_flags_triggers(app, tmp_path, monkeypatch):
    bars = [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i)
            for i in range(60)]                              # rising -> RSI overbought -> "short"
    write_bars_parquet(bars, tmp_path / "RISER" / "1m.parquet")
    monkeypatch.setattr(AlertsTab, "_catalog", lambda self: Catalog(str(tmp_path)))
    store = AlertStore(str(tmp_path / "a.json"))
    store.add(AlertRule("RISER", "RSI(14) 30/70", "short"))
    tab = AlertsTab(store=store)
    tab.check()
    assert "1 of 1" in tab._status.text()                    # the short alert fired
    assert tab._table.item(0, 3).text() == "TRIGGERED"
