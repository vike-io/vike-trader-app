"""AI strategy-development loop: generate -> pre-flight -> sandbox-run on OOS -> rank/repair.

The wedge: candidates are scored on a HELD-OUT (out-of-sample) slice and run in the sandbox, never
on the in-sample data the model effectively saw. ``develop_strategy`` runs one codegen+repair loop;
``develop_strategies`` generates N candidates ranked by OOS performance. The LLM client is injectable
(``ai.llm.ClaudeClient`` in production, a fake in tests) so the loop is fully testable offline.

Deferred (follow-ups): attaching the full walk_forward overfit Verdict to AI candidates (needs a
richer sandbox payload than as_dict); Anthropic prompt-caching + RAG grounding of the system prompt;
wiring AI candidates into the anti-overfit trial-ledger.
"""

from dataclasses import dataclass, field

from .llm import ToolSpec

STRATEGY_SYSTEM_PROMPT = """You write Python trading strategies that subclass `Strategy`. Emit ONE
complete, runnable subclass via the `submit_strategy` tool (code as a single string). No prose in code.

API CONTRACT (do not invent methods):
    from vike_trader_app.core.strategy import Strategy
    class Strategy:
        WARMUP: int = 0            # bars to skip before on_bar fires (your longest indicator lookback)
        def on_bar(self, bar) -> None        # bar: .open .high .low .close .volume .ts
        def buy(self, size); def sell(self, size); def close(self)
        def limit_buy(self, size, price); def stop_buy(self, size, price); def trailing_stop(self, size, trail)
        self.position   # signed float (>0 long, <0 short, 0 flat)
        self.equity     # float ; self.index  # current bar index ; self.bars(tf)  # higher-TF history
    PARAM_GRID = {"name": [v1, v2]}          # optional, for optimization

RULES (MUST/NEVER):
- MUST `from vike_trader_app.core.strategy import Strategy`, subclass it, implement `on_bar`.
- MUST only use the methods/attributes above. NEVER import os/sys/subprocess or touch files/network.
- MUST guard indicator warm-up (return early until enough history); never act on NaN; never read future bars.
Allowed imports: math, statistics, datetime, numpy, vike_trader_app.core.{strategy,model,indicators}.
"""


def submit_strategy_tool() -> ToolSpec:
    """The tool the model calls to deliver one complete Strategy subclass as source text."""
    return ToolSpec(
        name="submit_strategy",
        description="Submit the complete Python source of one Strategy subclass.",
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Full module source: imports + the Strategy subclass."},
                "explanation": {"type": "string", "description": "1-2 sentences on the entry/exit logic."},
            },
            "required": ["code"],
        },
    )


@dataclass
class AgentResult:
    """Outcome of developing one strategy: the code + its IS/OOS reports + accept status."""

    code: str
    explanation: str
    accepted: bool
    attempts: int
    problems: list = field(default_factory=list)
    is_report: object = None   # sandbox as_dict() on the in-sample slice (or None)
    oos_report: object = None  # sandbox as_dict() on the held-out (out-of-sample) slice (or None)


def _split(bars, holdout_frac):
    k = max(1, int(len(bars) * (1.0 - holdout_frac)))
    return bars[:k], bars[k:]


def develop_strategy(prompt, bars, *, client, config=None, max_repairs: int = 2,
                     holdout_frac: float = 0.3, timeout: float = 30.0) -> AgentResult:
    """One codegen -> pre-flight -> sandbox-on-OOS loop with bounded repair; scores on the OOS slice."""
    from ..core.sandbox import run_sandboxed
    from ..core.sandbox.preflight import check_strategy_source
    from ..tester import TesterConfig

    config = config or TesterConfig()
    is_bars, oos_bars = _split(bars, holdout_frac)
    if not oos_bars or not is_bars:
        return AgentResult(code="", explanation="", accepted=False, attempts=0,
                           problems=["not enough bars for an in-sample + out-of-sample split"])
    tools = [submit_strategy_tool()]
    user = prompt
    code = explanation = ""
    problems: list = []
    for attempt in range(1, max_repairs + 2):
        captured: dict = {}

        def _dispatch(name, args, _c=captured):
            if name == "submit_strategy":
                _c["code"] = args.get("code", "")
                _c["explanation"] = args.get("explanation", "")
                return {"ok": True}
            return {"error": f"unknown tool {name}"}

        try:
            client.run(STRATEGY_SYSTEM_PROMPT, user, tools, _dispatch)
        except Exception as exc:  # noqa: BLE001 - a flaky client becomes a repair attempt, not a crash
            problems = [f"client error: {type(exc).__name__}: {exc}"]
            user = "The previous attempt errored. Submit a strategy via submit_strategy."
            continue
        code = captured.get("code", "")
        explanation = captured.get("explanation", "")
        if not code:
            problems = ["model did not submit a strategy"]
            user = "You did not call submit_strategy with code. Call it now."
            continue
        problems = check_strategy_source(code)
        if problems:
            user = "Your strategy failed the safety pre-flight:\n" + "\n".join(problems) + "\nFix and resubmit."
            continue
        oos = run_sandboxed(code, oos_bars, config, timeout=timeout)
        if not oos.get("ok"):
            problems = [f"run error: {oos.get('error')}"]
            user = f"Your strategy failed to run out-of-sample: {oos.get('error')}. Fix and resubmit."
            continue
        if oos["report"]["n_trades"] == 0:
            problems = ["no trades on the out-of-sample window"]
            user = "Your strategy made no out-of-sample trades. Make the entry condition fire. Resubmit."
            continue
        is_res = run_sandboxed(code, is_bars, config, timeout=timeout)
        return AgentResult(code=code, explanation=explanation, accepted=True, attempts=attempt,
                           problems=[], is_report=is_res.get("report"), oos_report=oos["report"])
    return AgentResult(code=code, explanation=explanation, accepted=False,
                       attempts=max_repairs + 1, problems=problems)


def develop_strategies(prompt, bars, *, client, n: int = 3, criterion: str = "sharpe", **kw) -> list:
    """Generate ``n`` candidates; return them ranked best-first (accepted first, then OOS ``criterion``)."""
    results = [develop_strategy(prompt, bars, client=client, **kw) for _ in range(n)]

    def key(r):
        score = r.oos_report.get(criterion, float("-inf")) if r.oos_report else float("-inf")
        return (1 if r.accepted else 0, score)

    return sorted(results, key=key, reverse=True)
