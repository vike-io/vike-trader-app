"""LLM client agentic loop (mocked anthropic) + dispatch routing + run_chat (fake client)."""

import json
import types

import pytest

from vike_trader_app.ai.llm import ToolSpec, ToolCall, ClaudeClient, tool_specs, make_dispatch


def _block(**kw):
    return types.SimpleNamespace(**kw)


class _MockAnthropic:
    """Scripts two messages.create() responses: a tool_use turn, then a final text turn."""

    def __init__(self):
        self.calls = []
        self.messages = types.SimpleNamespace(create=self._create)
        self._step = 0

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        self._step += 1
        if self._step == 1:
            return _block(
                stop_reason="tool_use",
                content=[_block(type="tool_use", id="t1", name="run_sma_backtest",
                                input={"closes": [1.0, 2.0], "fast": 5, "slow": 20})],
            )
        return _block(stop_reason="end_turn", content=[_block(type="text", text="All done.")])


def test_claude_client_runs_tool_loop():
    mock = _MockAnthropic()
    client = ClaudeClient(client=mock)
    seen = {}

    def dispatch(name, args):
        seen["call"] = (name, args)
        return {"ok": True}

    tools = [ToolSpec("run_sma_backtest", "bt", {"type": "object", "properties": {}})]
    out = client.run("sys", "do a backtest", tools, dispatch)

    assert out == "All done."
    assert seen["call"][0] == "run_sma_backtest"
    assert seen["call"][1]["fast"] == 5
    assert len(mock.calls) == 2
    second_msgs = mock.calls[1]["messages"]
    tool_results = [b for m in second_msgs if isinstance(m.get("content"), list)
                    for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert tool_results and tool_results[0]["tool_use_id"] == "t1"


def test_claude_client_caches_system_and_tools():
    """Prompt caching: the system prompt is sent as a cache_control'd block and the last tool
    carries a cache_control breakpoint, so the static prefix is reused across turns/candidates."""
    mock = _MockAnthropic()
    client = ClaudeClient(client=mock)
    tools = [ToolSpec("a", "ta", {"type": "object", "properties": {}}),
             ToolSpec("b", "tb", {"type": "object", "properties": {}})]
    client.run("SYSTEM PROMPT", "go", tools, lambda n, a: {"ok": True})
    first = mock.calls[0]
    assert isinstance(first["system"], list)
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["system"][0]["text"] == "SYSTEM PROMPT"
    assert first["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in first["tools"][0]   # only the last tool needs the breakpoint


def test_dispatch_routes_to_services():
    dispatch = make_dispatch()
    out = dispatch("run_sma_backtest", {"closes": [100.0 + (i % 7) for i in range(60)],
                                        "fast": 5, "slow": 20, "fee_rate": 0.0})
    assert out["params"] == {"fast": 5, "slow": 20}
    with pytest.raises(ValueError):
        dispatch("nonexistent_tool", {})


def test_tool_specs_cover_the_five_services():
    specs = tool_specs()
    names = {s.name for s in specs}
    assert {"run_sma_backtest", "optimize_sma", "fetch_ohlcv", "overfit_check", "query_kb"} <= names
    for s in specs:
        assert s.input_schema.get("type") == "object"
        assert isinstance(s.description, str) and s.description


def test_run_chat_uses_injected_client_and_dispatch():
    from vike_trader_app.ai.cli import run_chat

    class _FakeClient:
        def __init__(self):
            self.seen = None

        def run(self, system, user, tools, dispatch, max_turns=8):
            self.seen = (system, user, tools)
            dispatch("query_kb", {"query": "compiled kernel", "k": 1})
            return f"answered: {user}"

    fake = _FakeClient()
    routed = {}

    def dispatch(name, args):
        routed["name"] = name
        return {"n": 0, "hits": []}

    out = run_chat("how fast is the engine?", client=fake, dispatch=dispatch)
    assert out == "answered: how fast is the engine?"
    assert routed["name"] == "query_kb"
    assert fake.seen[1] == "how fast is the engine?"
    assert fake.seen[2]
