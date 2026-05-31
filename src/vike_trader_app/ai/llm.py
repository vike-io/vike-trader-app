"""Provider-agnostic LLM client with an agentic tool-use loop, plus a Claude implementation.

``LLMClient.run(system, user, tools, dispatch)`` runs the whole loop and returns the final
assistant text, invoking ``dispatch(name, input) -> dict`` for each tool call. Provider-specific
message formatting stays inside the client. The anthropic dependency is behind the ``[ai]`` extra.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class ToolSpec:
    """A tool advertised to the model: name, description, and a JSON-Schema for inputs."""

    name: str
    description: str
    input_schema: dict


@dataclass
class ToolCall:
    """A model-requested tool invocation."""

    id: str
    name: str
    input: dict


class LLMClient:
    """Interface: run an agentic loop and return the final assistant text."""

    def run(self, system: str, user: str, tools: list[ToolSpec], dispatch, max_turns: int = 8) -> str:
        raise NotImplementedError


class ClaudeClient(LLMClient):
    """Anthropic (Claude) tool-use implementation."""

    def __init__(self, model: str = "claude-opus-4-8", max_tokens: int = 2048,
                 client=None, api_key: str | None = None):
        if client is None:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover - exercised only without the extra
                raise ImportError("CLI chat requires the extra: pip install vike_trader_app[ai]") from e
            client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def run(self, system: str, user: str, tools: list[ToolSpec], dispatch, max_turns: int = 8) -> str:
        anth_tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                      for t in tools]
        messages: list[dict] = [{"role": "user", "content": user}]
        final = ""
        for _ in range(max_turns):
            resp = self._client.messages.create(
                model=self._model, max_tokens=self._max_tokens, system=system,
                tools=anth_tools, messages=messages,
            )
            tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            texts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
            if texts:
                final = "\n".join(texts)
            if getattr(resp, "stop_reason", None) != "tool_use" or not tool_uses:
                return final
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for tu in tool_uses:
                try:
                    out = dispatch(tu.name, dict(tu.input))
                    content = json.dumps(out, default=str)
                except Exception as exc:  # surface tool errors back to the model
                    content = json.dumps({"error": str(exc)})
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content})
            messages.append({"role": "user", "content": results})
        return final


def tool_specs() -> list[ToolSpec]:
    """JSON-Schema specs for the five vike services exposed to the model."""
    num_array = {"type": "array", "items": {"type": "number"}}
    return [
        ToolSpec("run_sma_backtest", "Backtest an SMA(fast)x SMA(slow) crossover on close prices; returns metrics.",
                 {"type": "object",
                  "properties": {"closes": num_array, "fast": {"type": "integer"},
                                 "slow": {"type": "integer"}, "fee_rate": {"type": "number"}},
                  "required": ["closes", "fast", "slow"]}),
        ToolSpec("optimize_sma", "Sweep SMA crossover parameters over fasts x slows; returns top-N ranked combos.",
                 {"type": "object",
                  "properties": {"closes": num_array,
                                 "fasts": {"type": "array", "items": {"type": "integer"}},
                                 "slows": {"type": "array", "items": {"type": "integer"}},
                                 "fee_rate": {"type": "number"}, "top_n": {"type": "integer"}},
                  "required": ["closes", "fasts", "slows"]}),
        ToolSpec("fetch_ohlcv", "Fetch + cache OHLCV for a symbol; returns a summary incl. closes.",
                 {"type": "object",
                  "properties": {"symbol": {"type": "string"}, "interval": {"type": "string"},
                                 "start_ms": {"type": "integer"}, "end_ms": {"type": "integer"},
                                 "source": {"type": "string"}},
                  "required": ["symbol", "interval", "start_ms", "end_ms"]}),
        ToolSpec("overfit_check", "Deflated Sharpe + verdict for an observed Sharpe given all trial Sharpes.",
                 {"type": "object",
                  "properties": {"observed_sr": {"type": "number"}, "trial_sharpes": num_array,
                                 "n_obs": {"type": "integer"}, "n_splits": {"type": "integer"}},
                  "required": ["observed_sr", "trial_sharpes", "n_obs"]}),
        ToolSpec("query_kb", "Search the vike-trader codebase/knowledge base; returns top-k passages.",
                 {"type": "object",
                  "properties": {"query": {"type": "string"}, "k": {"type": "integer"}},
                  "required": ["query"]}),
    ]


def make_dispatch():
    """Return a ``dispatch(name, args) -> dict`` routing tool calls to ``ai.services``."""
    from . import services

    def dispatch(name: str, args: dict) -> dict:
        if name == "run_sma_backtest":
            return services.run_sma_backtest(args["closes"], args["fast"], args["slow"],
                                             fee_rate=args.get("fee_rate", 0.0))
        if name == "optimize_sma":
            return services.optimize_sma(args["closes"], args["fasts"], args["slows"],
                                         fee_rate=args.get("fee_rate", 0.0),
                                         top_n=args.get("top_n", 10))
        if name == "fetch_ohlcv":
            return services.fetch_ohlcv(args["symbol"], args["interval"], args["start_ms"],
                                        args["end_ms"], source=args.get("source", "binance"))
        if name == "overfit_check":
            return services.overfit_check(args["observed_sr"], args["trial_sharpes"], args["n_obs"],
                                          n_splits=args.get("n_splits", 4))
        if name == "query_kb":
            return services.query_kb(args["query"], k=args.get("k", 5))
        raise ValueError(f"unknown tool: {name!r}")

    return dispatch
