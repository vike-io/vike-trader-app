"""AI strategy-development loop: overfit Verdict attachment + RAG grounding of the system prompt.

The sandbox is monkeypatched (we're testing the agent's orchestration, not subprocess execution);
pre-flight runs for real on a clean strategy string.
"""

import pytest

from vike_trader_app.ai import agent as agent_mod
from vike_trader_app.ai.agent import develop_strategy, develop_strategies

_CLEAN = """from vike_trader_app.core.strategy import Strategy

class S(Strategy):
    def on_bar(self, bar):
        if self.index % 4 == 0:
            self.buy(1.0)
        elif self.index % 4 == 2:
            self.close()
"""


class _FakeClient:
    """Submits one fixed strategy and records every system prompt it was given."""

    def __init__(self, code=_CLEAN):
        self.code = code
        self.systems = []

    def run(self, system, user, tools, dispatch, max_turns=8):
        self.systems.append(system)
        dispatch("submit_strategy", {"code": self.code, "explanation": "cycle in/out every 4 bars"})
        return "ok"


@pytest.fixture
def fake_sandbox(monkeypatch):
    """Return ok reports: each develop_strategy calls run_sandboxed twice (OOS then IS)."""
    state = {"n": 0}

    def _run(code, bars, config, timeout=30.0):
        state["n"] += 1
        sharpe = 1.5 if state["n"] % 2 == 1 else 2.5   # odd call = OOS (1.5), even = IS (2.5)
        return {"ok": True, "report": {"n_trades": 5, "sharpe": sharpe}}

    monkeypatch.setattr("vike_trader_app.core.sandbox.run_sandboxed", _run)
    return state


def _bars(n=40):
    from vike_trader_app.core.model import Bar
    return [Bar(ts=i * 60_000, open=100.0, high=101.0, low=99.0, close=100.0 + i, volume=1.0)
            for i in range(n)]


def test_develop_strategy_attaches_overfit_verdict(fake_sandbox):
    r = develop_strategy("an MA strategy", _bars(), client=_FakeClient())
    assert r.accepted
    assert r.overfit is not None
    assert r.overfit["level"] in ("Low", "Medium", "High")
    assert "deflated_sharpe" in r.overfit
    assert r.overfit["oos_sharpe"] == pytest.approx(1.5)
    assert r.overfit["is_sharpe"] == pytest.approx(2.5)
    assert r.overfit["n_trials"] == 1


def test_develop_strategies_deflates_against_all_trials(fake_sandbox):
    results = develop_strategies("an MA strategy", _bars(), client=_FakeClient(), n=3)
    accepted = [r for r in results if r.accepted]
    assert len(accepted) == 3
    for r in accepted:
        assert r.overfit is not None
        assert r.overfit["n_trials"] == 3   # deflated against all three candidate trials


def test_rag_retrieve_grounds_the_system_prompt(fake_sandbox):
    client = _FakeClient()
    marker = "REFERENCE: prefer ATR-based stops on crypto."
    develop_strategy("an MA strategy", _bars(), client=client,
                     retrieve=lambda q: [marker])
    assert client.systems and marker in client.systems[0]
    assert "subclass `Strategy`" in client.systems[0]   # original system prompt still present


def test_rag_retriever_error_is_swallowed(fake_sandbox):
    def _boom(_q):
        raise RuntimeError("kb down")

    r = develop_strategy("x", _bars(), client=_FakeClient(), retrieve=_boom)
    assert r.accepted   # RAG failure must not break codegen
