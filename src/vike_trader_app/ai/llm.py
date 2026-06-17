"""Provider-agnostic LLM client with an agentic tool-use loop, plus a Claude implementation.

``LLMClient.run(system, user, tools, dispatch)`` runs the whole loop and returns the final
assistant text, invoking ``dispatch(name, input) -> dict`` for each tool call. Provider-specific
message formatting stays inside the client. The anthropic dependency is behind the ``[ai]`` extra.
"""

from __future__ import annotations

import json
import os
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
        # Prompt caching: the system prompt + tool defs are large and IDENTICAL across every turn of
        # this loop AND across repeated candidate generations, so mark them cacheable (ephemeral, 5min
        # TTL). A cache_control breakpoint on the last tool covers the whole tools block; one on the
        # system block covers the system prompt. The API silently ignores it when too short to cache.
        if anth_tools:
            anth_tools[-1] = {**anth_tools[-1], "cache_control": {"type": "ephemeral"}}
        system_param = ([{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
                        if system else system)
        messages: list[dict] = [{"role": "user", "content": user}]
        final = ""
        for _ in range(max_turns):
            resp = self._client.messages.create(
                model=self._model, max_tokens=self._max_tokens, system=system_param,
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


class CerebrasClient(LLMClient):
    """Cerebras Cloud tool-use implementation via the official ``cerebras_cloud_sdk`` — fast, low-cost
    open models on wafer-scale inference.

    BYO key: reads ``CEREBRAS_API_KEY`` from the environment (or pass ``api_key=``). Runs the SAME
    agentic loop as ``ClaudeClient``, in OpenAI-style function-calling format (the Cerebras SDK mirrors
    it). Default model Llama 3.3 70B; the validate->fix loop covers most code-quality gaps — keep
    Claude for hard cases.
    """

    def __init__(self, model: str = "llama-3.3-70b", max_tokens: int = 2048,
                 client=None, api_key: str | None = None):
        if client is None:
            try:
                from cerebras.cloud.sdk import Cerebras
            except ImportError as e:  # pragma: no cover - exercised only without the extra
                raise ImportError("Cerebras chat requires: pip install cerebras_cloud_sdk") from e
            key = api_key or os.environ.get("CEREBRAS_API_KEY")
            if not key:
                raise ValueError("set CEREBRAS_API_KEY (or pass api_key=) to use Cerebras")
            client = Cerebras(api_key=key)
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def run(self, system: str, user: str, tools: list[ToolSpec], dispatch, max_turns: int = 8) -> str:
        oai_tools = [{"type": "function",
                      "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
                     for t in tools]
        messages: list[dict] = [{"role": "system", "content": system},
                                {"role": "user", "content": user}]
        final = ""
        for _ in range(max_turns):
            resp = self._client.chat.completions.create(
                model=self._model, max_tokens=self._max_tokens, messages=messages,
                tools=oai_tools or None, tool_choice="auto" if oai_tools else None,
            )
            msg = resp.choices[0].message
            if getattr(msg, "content", None):
                final = msg.content
            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                return final
            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                               for tc in tool_calls],
            })
            for tc in tool_calls:
                try:
                    out = dispatch(tc.function.name, json.loads(tc.function.arguments or "{}"))
                    content = json.dumps(out, default=str)
                except Exception as exc:  # surface tool errors back to the model
                    content = json.dumps({"error": str(exc)})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
        return final


def make_client(provider: str = "claude", **kw) -> LLMClient:
    """Construct an LLM client by provider name: 'claude' (Anthropic) or 'cerebras' (BYO key)."""
    p = (provider or "claude").lower()
    if p in ("claude", "anthropic"):
        return ClaudeClient(**kw)
    if p == "cerebras":
        return CerebrasClient(**kw)
    raise ValueError(f"unknown LLM provider {provider!r}; expected 'claude' or 'cerebras'")


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
