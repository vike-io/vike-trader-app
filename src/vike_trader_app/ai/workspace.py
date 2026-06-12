"""Agent-emitted workspaces (Phase 5).

The in-app Claude agent turns a plain-language layout request ("give me a 4-chart BTC/ETH/SOL
momentum board on 1h, all linked") into a structured workspace *spec* by calling the
``create_workspace`` tool. The shell converts that spec into a SessionState
(``ui.workspaces.workspace_from_agent_spec``) and applies it. The spec is plain JSON — the seam
that lets an LLM drive the layout, which neither AmiBroker nor MultiCharts can do.

This module is UI-free (depends only on ``llm``): it defines the tool and runs the loop,
returning the captured spec dict. Validation/conversion to a SessionState lives in the ui layer.
"""

from __future__ import annotations

from .llm import LLMClient, ToolSpec

# Kept in sync with the shell: the only persistent spaces are Chart + Studio. The 7 tools
# (screener/journal/alerts/data/news/calendar/options) are on-demand docks, not spaces.
SPACES = ["chart", "studio"]
INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"]
MAX_LINK_GROUP = 6


def create_workspace_tool() -> ToolSpec:
    return ToolSpec(
        name="create_workspace",
        description=(
            "Lay out the trading workspace for the user's request. Open one chart document per "
            "instrument they want to watch; set each chart's interval; give charts that should "
            "move together the SAME link_group colour (1-6; 0 = unlinked); choose the active "
            "space and which side panels (market watch, trades) are open. Call this exactly once."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "space": {"type": "string", "enum": SPACES,
                          "description": "the active space to show"},
                "documents": {
                    "type": "array",
                    "description": "chart documents to open, in order",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "e.g. BTCUSDT, AAPL"},
                            "interval": {"type": "string", "enum": INTERVALS},
                            "indicators": {"type": "array", "items": {"type": "string"},
                                           "description": "indicator names, e.g. rsi, macd"},
                            "link_group": {"type": "integer", "minimum": 0,
                                           "maximum": MAX_LINK_GROUP},
                        },
                        "required": ["symbol"],
                    },
                },
                "panels": {
                    "type": "object",
                    "properties": {"market": {"type": "boolean"},
                                   "trades": {"type": "boolean"}},
                },
                "watchlist_link": {"type": "integer", "minimum": 0, "maximum": MAX_LINK_GROUP},
            },
            "required": ["documents"],
        },
    )


_SYSTEM = (
    "You are the layout assistant for a desktop trading terminal. Translate the user's request "
    "into a single create_workspace call. Prefer one chart document per instrument. When the "
    "user wants charts to move together (a 'linked' or 'synced' board), give them the same "
    "link_group. Use sensible intervals (default 1h). Do not write prose — just call the tool."
)


def develop_workspace(prompt: str, *, client: LLMClient, max_turns: int = 3) -> dict | None:
    """Run the agent loop for one create_workspace call. Returns the captured spec dict, or
    ``None`` if the model never called the tool."""
    captured: dict = {}

    def _dispatch(name: str, args: dict) -> dict:
        if name == "create_workspace":
            captured["spec"] = args
            return {"ok": True}
        return {"error": f"unknown tool {name}"}

    client.run(_SYSTEM, prompt, [create_workspace_tool()], _dispatch, max_turns=max_turns)
    return captured.get("spec")
