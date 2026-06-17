"""Out-of-process sandbox: subprocess + timeout boundary."""

from vike_trader_app.core.model import Bar
from vike_trader_app.core.sandbox import _child_env, run_sandboxed
from vike_trader_app.tester import TesterConfig


def _bars(n=6):
    return [Bar(ts=i * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.0 + i)
            for i in range(n)]


_BUYHOLD = """
from vike_trader_app.core.strategy import Strategy

class S(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
"""

_HANG = """
from vike_trader_app.core.strategy import Strategy

class S(Strategy):
    def on_bar(self, bar):
        while True:
            pass
"""

_RAISES = """
from vike_trader_app.core.strategy import Strategy

class S(Strategy):
    def on_bar(self, bar):
        raise RuntimeError("boom")
"""


def test_valid_strategy_runs_sandboxed():
    res = run_sandboxed(_BUYHOLD, _bars(), TesterConfig(taker_fee=0.0))
    assert res["ok"] is True
    assert res["report"]["n_trades"] >= 0
    assert "total_return" in res["report"]


def test_hanging_strategy_times_out():
    res = run_sandboxed(_HANG, _bars(), TesterConfig(), timeout=3.0)
    assert res["ok"] is False
    assert res["error"] == "timeout"


def test_raising_strategy_reported_not_crash():
    res = run_sandboxed(_RAISES, _bars(), TesterConfig())
    assert res["ok"] is False
    assert "boom" in res["error"] or "RuntimeError" in res["error"]


def test_malicious_source_rejected_in_child():
    bad = ("import os\nfrom vike_trader_app.core.strategy import Strategy\n"
           "class S(Strategy):\n    def on_bar(self, bar): pass\n")
    res = run_sandboxed(bad, _bars(), TesterConfig())
    assert res["ok"] is False
    assert "pre-flight" in res["error"] or "not allowed" in res["error"]


def test_child_env_excludes_secrets_keeps_essentials(monkeypatch):
    """The sandbox child must NOT inherit API keys/tokens (a strategy could read os.environ and
    egress them), but MUST keep what CPython needs to start. (test_valid_strategy_runs_sandboxed
    above already proves the child still imports + runs under this scrubbed env.)"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "SECRET-must-not-leak")
    monkeypatch.setenv("VIKE_SOME_TOKEN", "also-secret")
    env = _child_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "VIKE_SOME_TOKEN" not in env
    assert "PATH" in env                       # essentials retained so the child can start + import
