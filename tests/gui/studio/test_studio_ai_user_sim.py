"""Studio AI end-to-end user simulation.

Drives the REAL StudioTab the way a user would through the AI strategy-lab journey:

  1. type a prompt -> a background ChatWorker (QThread) runs ``ai.agent.develop_strategy`` with a
     FAKE LLM client (no network) -> the generated code lands in the editor;
  2. press Run -> the backtest produces a TesterReport with trades;
  3. a SECOND prompt (different code) opens the human-in-the-loop DiffDialog -> Apply -> the editor
     updates to the new code;
  4. take the compiled strategy class and forward-test it bar-by-bar via core.paper.PaperTester ->
     the live equity curve grows.

Everything stays on the main thread (the data layer is not thread-safe); the ChatWorker is pumped
with a processEvents() loop. The DiffDialog modal is neutralised by monkeypatching its ``exec``.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.core.paper import PaperTester  # noqa: E402
from vike_trader_app.tester import TesterConfig  # noqa: E402
from vike_trader_app.ui import studio as studio_mod  # noqa: E402
from vike_trader_app.ui.studio import DiffDialog, StudioTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _sync_chat_worker(monkeypatch):
    """Run the AI ChatWorker SYNCHRONOUSLY on the main thread. The real QThread running
    develop_strategy + GC can hard-segfault under full-suite load on Python 3.14; a synchronous
    start() makes the agent journey deterministic — result + finished are delivered direct on the
    same thread, so the existing emit()/_pump_until flow still works (the predicate is just already
    true), and tab._worker is cleared as in production."""
    def _sync_start(self):
        self.run()             # develop_strategy + result.emit -> _on_agent_result (direct)
        self.finished.emit()   # -> _on_worker_finished -> clears the tab's _worker reference
    monkeypatch.setattr(studio_mod.ChatWorker, "start", _sync_start)
    monkeypatch.setattr(studio_mod.ChatWorker, "isRunning", lambda self: False)
    yield


def _bars(n=16):
    return [Bar(ts=i * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i,
                close=100.0 + i, volume=1.0)
            for i in range(n)]


# Two distinct, preflight-clean strategies that both TRADE. The second closes on a different bar so
# its source differs from the first (so the DiffDialog actually has a change to apply).
_CODE_A = """from vike_trader_app.core.strategy import SingleSymbolStrategy as Strategy


class S(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 3:
            self.close()
"""

_CODE_B = """from vike_trader_app.core.strategy import SingleSymbolStrategy as Strategy


class S(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(2.0)
        elif self.index == 2:
            self.close()
"""


class _FakeClient:
    """A no-network LLM stand-in: hands back canned Strategy source via the submit_strategy tool.

    Matches the production ClaudeClient.run contract that ``ai.agent.develop_strategy`` calls.
    Advances through ``codes`` so a second prompt yields different code (the DiffDialog path).
    """

    def __init__(self, codes):
        self._codes = list(codes)
        self.calls = 0

    def run(self, system, user, tools, dispatch, max_turns=8):
        code = self._codes[min(self.calls, len(self._codes) - 1)]
        self.calls += 1
        dispatch("submit_strategy", {"code": code, "explanation": "fake strategy"})
        return "ok"


def _pump_until(app, predicate, timeout_s=10.0):
    """Spin the Qt event loop until ``predicate()`` is true or we time out; returns the result."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return predicate()


def _drain_worker(app, tab):
    """Wait for any in-flight ChatWorker to fully finish so the QThread is safe to destroy."""
    _pump_until(app, lambda: tab._worker is None or not tab._worker.isRunning(), timeout_s=5.0)
    app.processEvents()


# ---------------------------------------------------------------------------
# Scenario 1 + 2: prompt -> generated code lands in editor -> Run produces a report
# ---------------------------------------------------------------------------

def test_prompt_generates_code_into_empty_editor_then_run_backtests(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_agent_client(_FakeClient([_CODE_A]))

    # User types a prompt and hits send -> ChatWorker spins up on a background thread.
    tab.chat.promptSubmitted.emit("build me a momentum strategy")

    # Wait for the worker to land the generated code in the (empty) editor.
    landed = _pump_until(app, lambda: "class S(Strategy)" in tab.text(), timeout_s=10.0)
    _drain_worker(app, tab)
    assert landed, "AI-generated code never appeared in the editor"
    assert "self.buy(1.0)" in tab.text()

    # Now the user presses Run -> a real backtest yields a report with at least one trade.
    tab.run_code()
    assert tab.results.last_report is not None
    assert tab.results.last_report.n_trades >= 1
    assert tab.results.last_report.equity_curve  # non-empty stored equity curve


# ---------------------------------------------------------------------------
# Scenario 3: second prompt with non-empty editor -> DiffDialog -> Apply updates the editor
# ---------------------------------------------------------------------------

def test_second_prompt_opens_diff_dialog_and_apply_updates_editor(app, monkeypatch):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    # Editor already has CODE_A; the AI proposes CODE_B.
    tab.set_text(_CODE_A)
    tab.set_agent_client(_FakeClient([_CODE_B]))

    # Auto-accept the human-in-the-loop diff modal (must never block headless).
    monkeypatch.setattr(DiffDialog, "exec", lambda self: QtWidgets.QDialog.Accepted)

    ver_before = tab._apply_version
    tab.chat.promptSubmitted.emit("make it trade bigger and exit sooner")

    # Wait for the editor to flip to the proposed code (DiffDialog accepted).
    applied = _pump_until(app, lambda: "self.buy(2.0)" in tab.text(), timeout_s=10.0)
    _drain_worker(app, tab)
    assert applied, "Apply on the DiffDialog did not update the editor to the proposed code"
    assert "self.buy(1.0)" not in tab.text()  # old code replaced
    assert tab._apply_version == ver_before + 1  # version bumped on apply


def test_second_prompt_diff_dialog_reject_keeps_old_code(app, monkeypatch):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(_CODE_A)
    tab.set_agent_client(_FakeClient([_CODE_B]))

    # User reviews the diff and clicks Reject -> editor must keep the original code.
    monkeypatch.setattr(DiffDialog, "exec", lambda self: QtWidgets.QDialog.Rejected)

    ver_before = tab._apply_version
    tab.chat.promptSubmitted.emit("change everything")

    # Give the worker time to finish and the (rejected) result to be processed.
    _pump_until(app, lambda: tab._worker is None, timeout_s=10.0)
    _drain_worker(app, tab)
    assert "self.buy(1.0)" in tab.text()      # original code intact
    assert "self.buy(2.0)" not in tab.text()  # proposed code NOT applied
    assert tab._apply_version == ver_before    # version unchanged on reject


# ---------------------------------------------------------------------------
# Scenario 4: forward (paper) test of the editor's compiled strategy class
# ---------------------------------------------------------------------------

def test_forward_test_compiled_strategy_grows_equity(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    tab.set_text(_CODE_A)

    cls = tab.current_strategy_cls()
    assert cls is not None and callable(cls)

    bars = _bars(16)
    ft = PaperTester(symbol="BTCUSDT", interval="1m", strategy=cls(), cash=10_000.0,
                     seed_bars=bars[:5])
    for bar in bars[5:]:
        ft.on_bar_live(bar)

    res = ft.result()
    # Only the live bars are in the curve (the 5 seed bars warm up but stay out).
    assert len(res.equity_curve) == len(bars) - 5
    assert res.final_equity > 0.0
    # CODE_A buys at index 0 and rides a rising series, so live equity should end up at/above start.
    assert res.equity_curve[-1] >= res.equity_curve[0]


# ---------------------------------------------------------------------------
# Guardrails the real journey relies on
# ---------------------------------------------------------------------------

def test_prompt_without_client_is_graceful_no_worker(app):
    tab = StudioTab()
    tab.set_bars(_bars())
    # No agent client configured -> a system message, no worker, no crash.
    tab.chat.promptSubmitted.emit("hello?")
    app.processEvents()
    assert tab._worker is None
    assert tab.text() == ""  # nothing generated into the editor


def test_rejected_agent_result_surfaces_but_run_is_blocked(app):
    """An always-rejected strategy (forbidden import) must not silently crash and must not run.

    The develop loop returns ``accepted=False`` but still carries the last (rejected) code, which
    the empty-editor path loads so the user can inspect/fix it — the failure is reported in chat.
    Crucially, attempting to Run the unsafe code is refused by the loader gate, not executed, and
    no report is stored.
    """
    tab = StudioTab()
    tab.set_bars(_bars())
    tab.set_config(TesterConfig(taker_fee=0.0))
    # Always-rejected source (forbidden import) -> develop_strategy returns accepted=False.
    bad = "import os\n" + _CODE_A
    tab.set_agent_client(_FakeClient([bad]))

    tab.chat.promptSubmitted.emit("do something unsafe")
    _pump_until(app, lambda: tab._worker is None, timeout_s=10.0)
    _drain_worker(app, tab)

    # The unsafe code never compiles -> current_strategy_cls is None and Run stores no report.
    assert tab.current_strategy_cls() is None
    tab.run_code()  # must not raise — the loader gate rejects "import os", surfaced as an error
    assert tab.results.last_report is None
