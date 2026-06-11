"""Offscreen tests for agent-emitted workspaces (Phase 5): spec -> apply, and the threaded glue."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6QtAds")

from PySide6 import QtWidgets  # noqa: E402

import vike_trader_app.ui.chartdoc as chartdoc  # noqa: E402
from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.dataload import LoadResult  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _synthetic_load(monkeypatch):
    bars = [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i)
            for i in range(30)]
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(list(bars)))


class _FakeClient:
    """Simulates Claude calling create_workspace once with a fixed spec."""

    def __init__(self, spec):
        self._spec = spec

    def run(self, system, user, tools, dispatch, max_turns=8):
        dispatch("create_workspace", self._spec)
        return "ok"


def test_apply_agent_spec_opens_linked_documents(app):
    win = MainWindow(session_path=None)
    spec = {"space": "chart", "watchlist_link": 1,
            "documents": [{"symbol": "BTCUSDT", "interval": "1h", "link_group": 1},
                          {"symbol": "ETHUSDT", "interval": "1h", "link_group": 1},
                          {"symbol": "SOLUSDT", "interval": "4h", "link_group": 1}]}
    win._apply_agent_spec(spec)
    assert [d.symbol for d in win._doc_widgets] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert all(d.link_group == 1 for d in win._doc_widgets)
    assert win._doc_widgets[2].interval == "4h"
    assert win._watchlist_link == 1
    win.close()


def test_ai_generate_layout_threaded_applies(app):
    win = MainWindow(session_path=None)
    spec = {"documents": [{"symbol": "ADAUSDT", "interval": "1h"},
                          {"symbol": "DOTUSDT", "interval": "1h"}]}
    win._ai_generate_layout("a two-chart board", client=_FakeClient(spec))
    win._layout_workers[-1].wait(3000)   # let the worker thread finish develop_workspace
    app.processEvents()                  # deliver the queued done signal -> _on_ai_layout
    assert [d.symbol for d in win._doc_widgets] == ["ADAUSDT", "DOTUSDT"]
    win.close()


def test_superseded_layout_request_does_not_clobber(app):
    """A second AI-layout request disconnects the first, so a still-running first worker can't
    overwrite the newer layout when its (slow) API call eventually returns."""
    import threading

    gate = threading.Event()

    class _BlockingClient:
        def __init__(self, spec):
            self._spec = spec

        def run(self, system, user, tools, dispatch, max_turns=8):
            gate.wait(5)                       # block as a slow API call would
            dispatch("create_workspace", self._spec)
            return "ok"

    win = MainWindow(session_path=None)
    first = {"documents": [{"symbol": "BTCUSDT", "interval": "1h"}]}
    second = {"documents": [{"symbol": "ETHUSDT", "interval": "1h"},
                            {"symbol": "SOLUSDT", "interval": "1h"}]}
    win._ai_generate_layout("first", client=_BlockingClient(first))   # starts, blocks on gate
    win._ai_generate_layout("second", client=_FakeClient(second))     # supersedes, applies fast
    win._layout_workers[-1].wait(3000)   # wait the SECOND worker before asserting — its done
    app.processEvents()                  # signal is async, so processEvents alone could race it
    assert [d.symbol for d in win._doc_widgets] == ["ETHUSDT", "SOLUSDT"]
    gate.set()                                  # release the first worker — it must NOT clobber
    for w in list(win._layout_workers):
        w.wait(3000)
    app.processEvents()
    assert [d.symbol for d in win._doc_widgets] == ["ETHUSDT", "SOLUSDT"]
    win.close()


def test_ai_generate_layout_no_client_is_noop(app, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    win = MainWindow(session_path=None)
    win._ai_generate_layout("anything")  # no client, no key -> status message, no docs
    assert win.tabs.document_count() == 0
    win.close()


def test_ai_layout_command_in_palette(app):
    win = MainWindow(session_path=None)
    labels = [label for label, _cb in win._commands()]
    assert "AI: generate a layout…" in labels
    win.close()
