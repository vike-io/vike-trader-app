"""Unit tests for the agent-emitted workspace tool loop (Phase 5), with a fake LLM client."""

from vike_trader_app.ai.workspace import create_workspace_tool, develop_workspace


class _FakeClient:
    """Simulates the model calling create_workspace once with a fixed spec."""

    def __init__(self, spec=None):
        self._spec = spec

    def run(self, system, user, tools, dispatch, max_turns=8):
        assert any(t.name == "create_workspace" for t in tools)
        if self._spec is not None:
            dispatch("create_workspace", self._spec)
        return "ok"


def test_tool_schema_shape():
    t = create_workspace_tool()
    assert t.name == "create_workspace"
    props = t.input_schema["properties"]
    assert "documents" in props and "space" in props and "watchlist_link" in props
    assert t.input_schema["required"] == ["documents"]


def test_develop_workspace_captures_spec():
    spec = {"documents": [{"symbol": "BTCUSDT", "interval": "1h"}], "space": "chart"}
    assert develop_workspace("4-chart board", client=_FakeClient(spec)) == spec


def test_develop_workspace_none_when_tool_not_called():
    assert develop_workspace("hello", client=_FakeClient(None)) is None


def test_unknown_tool_is_rejected():
    seen = {}

    class Client:
        def run(self, system, user, tools, dispatch, max_turns=8):
            seen["res"] = dispatch("bogus", {})
            return ""

    develop_workspace("x", client=Client())
    assert "error" in seen["res"]
