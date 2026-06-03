# tests/test_symbol_suggest.py
"""AI symbol suggester — wraps an LLMClient, parses its reply into a symbol list."""

import pytest

from vike_trader_app.ai.symbol_suggest import suggest_symbols


class _FakeClient:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    def run(self, system, user, tools, dispatch, max_turns=8):
        self.calls.append((system, user, tools))
        return self._reply


def test_suggest_parses_reply_into_symbols():
    client = _FakeClient("Here you go:\nBTCUSDT, ETHUSDT SOLUSDT")
    out = suggest_symbols("top 3 crypto majors", client=client)
    assert out == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert client.calls[0][2] == []  # no tools advertised


def test_suggest_passes_group_hint_in_system_prompt():
    client = _FakeClient("EURUSD GBPUSD")
    suggest_symbols("majors", client=client, group="Dukascopy")
    system = client.calls[0][0]
    assert "Dukascopy" in system


def test_missing_extra_raises_friendly(monkeypatch):
    import vike_trader_app.ai.symbol_suggest as mod

    def _boom(*a, **k):
        raise ImportError("requires the extra: pip install vike_trader_app[ai]")

    monkeypatch.setattr(mod, "ClaudeClient", _boom)
    with pytest.raises(ImportError):
        suggest_symbols("anything")
