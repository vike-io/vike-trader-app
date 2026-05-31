"""AI agent loop with an injected fake client (no network)."""

from vike_trader_app.ai.agent import AgentResult, develop_strategies, develop_strategy
from vike_trader_app.core.model import Bar
from vike_trader_app.tester import TesterConfig


class _FakeClient:
    """Submits canned strategy code via the submit_strategy tool; advances through ``codes``."""

    def __init__(self, codes):
        self._codes = list(codes)
        self.calls = 0

    def run(self, system, user, tools, dispatch, max_turns=8):
        code = self._codes[min(self.calls, len(self._codes) - 1)]
        self.calls += 1
        dispatch("submit_strategy", {"code": code, "explanation": "test strategy"})
        return "submitted"


def _bars(n=20):
    return [Bar(ts=i * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.0 + i)
            for i in range(n)]


_GOOD = """
from vike_trader_app.core.strategy import Strategy

class S(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 2:
            self.close()
"""

_BAD_IMPORT = "import os\n" + _GOOD

_NOTRADE = """
from vike_trader_app.core.strategy import Strategy

class S(Strategy):
    def on_bar(self, bar):
        pass
"""


def test_good_strategy_accepted_first_try():
    res = develop_strategy("make a strategy", _bars(), client=_FakeClient([_GOOD]),
                           config=TesterConfig(taker_fee=0.0))
    assert isinstance(res, AgentResult)
    assert res.accepted is True
    assert res.attempts == 1
    assert res.oos_report["n_trades"] >= 1
    assert res.is_report is not None


def test_repairs_after_preflight_rejection():
    res = develop_strategy("x", _bars(), client=_FakeClient([_BAD_IMPORT, _GOOD]),
                           config=TesterConfig())
    assert res.accepted is True
    assert res.attempts == 2


def test_always_bad_is_rejected():
    res = develop_strategy("x", _bars(), client=_FakeClient([_BAD_IMPORT]), max_repairs=2)
    assert res.accepted is False
    assert res.problems
    assert res.attempts == 3


def test_no_trade_strategy_not_accepted():
    res = develop_strategy("x", _bars(), client=_FakeClient([_NOTRADE]), max_repairs=1)
    assert res.accepted is False
    assert any("trade" in p for p in res.problems)


def test_develop_strategies_ranks_candidates():
    out = develop_strategies("x", _bars(), client=_FakeClient([_GOOD]), n=2,
                             config=TesterConfig(taker_fee=0.0))
    assert len(out) == 2
    assert out[0].accepted is True


def test_empty_data_fails_honestly():
    res = develop_strategy("x", _bars(1), client=_FakeClient([_GOOD]))
    assert res.accepted is False
    assert any("split" in p for p in res.problems)


def test_raising_client_does_not_crash():
    class _Boom:
        def run(self, *a, **k):
            raise RuntimeError("api down")
    res = develop_strategy("x", _bars(), client=_Boom(), max_repairs=1)
    assert isinstance(res, AgentResult)
    assert res.accepted is False
