"""Tests for ai.llm.CerebrasClient — the OpenAI-style tool-use loop (stubbed SDK client)."""

import json
from types import SimpleNamespace

import pytest

from vike_trader_app.ai import llm


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _tool_call(tid, name, arguments):
    return SimpleNamespace(id=tid, type="function",
                           function=SimpleNamespace(name=name, arguments=arguments))


def _resp(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _StubCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return self._responses.pop(0)


class _StubClient:
    """Mimics cerebras_cloud_sdk.Cerebras (and the OpenAI client): .chat.completions.create(...)."""

    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=_StubCompletions(responses))


def test_tool_loop_dispatches_then_returns_final():
    stub = _StubClient([
        _resp(_msg(tool_calls=[_tool_call("c1", "submit_strategy", json.dumps({"code": "X"}))])),
        _resp(_msg(content="done: sharpe 1.2")),
    ])
    seen = {}

    def dispatch(name, args):
        seen[name] = args
        return {"ok": True}

    tools = [llm.ToolSpec("submit_strategy", "submit a strategy", {"type": "object", "properties": {}})]
    out = llm.CerebrasClient(client=stub, model="llama-3.3-70b").run("sys", "make a strat", tools, dispatch)

    assert out == "done: sharpe 1.2"
    assert seen == {"submit_strategy": {"code": "X"}}
    calls = stub.chat.completions.calls
    # tools were advertised in OpenAI function format
    assert calls[0]["tools"][0]["function"]["name"] == "submit_strategy"
    # the tool result was fed back on the second request as a role=tool message
    assert any(m.get("role") == "tool" and m["tool_call_id"] == "c1" for m in calls[1]["messages"])


def test_tool_error_is_surfaced_not_raised():
    stub = _StubClient([
        _resp(_msg(tool_calls=[_tool_call("c1", "submit_strategy", "{}")])),
        _resp(_msg(content="ok")),
    ])

    def dispatch(name, args):
        raise RuntimeError("boom")

    tools = [llm.ToolSpec("submit_strategy", "x", {"type": "object", "properties": {}})]
    out = llm.CerebrasClient(client=stub, model="m").run("s", "u", tools, dispatch)
    assert out == "ok"
    tool_msg = next(m for m in stub.chat.completions.calls[1]["messages"] if m.get("role") == "tool")
    assert "boom" in tool_msg["content"]


def test_make_client_selects_provider():
    assert isinstance(llm.make_client("cerebras", client=_StubClient([])), llm.CerebrasClient)
    with pytest.raises(ValueError):
        llm.make_client("bogus")
