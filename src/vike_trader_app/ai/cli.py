"""`vike chat` — an agentic CLI over the vike engine tools (typer).

``run_chat`` is the testable core (dependency-injected client/dispatch/tools). The typer ``chat``
command wires the real ``ClaudeClient`` + service dispatch. Requires the ``[ai]`` + ``[cli]`` extras.
"""

from __future__ import annotations

SYSTEM_PROMPT = (
    "You are a quantitative trading assistant for the vike-trader engine. Use the provided tools "
    "to fetch OHLCV data, run and optimize SMA-crossover backtests, check for overfitting "
    "(Deflated Sharpe / PBO), and search the codebase knowledge base. Prefer calling a tool over "
    "guessing. Summarize results clearly with the key metrics."
)


def run_chat(prompt: str, *, client=None, dispatch=None, tools=None) -> str:
    """Run one chat turn (agentic tool loop) and return the assistant's final text.

    ``client``/``dispatch``/``tools`` are injectable for testing; defaults use ClaudeClient + the
    service dispatch + the full tool specs.
    """
    from .llm import ClaudeClient, make_dispatch, tool_specs

    client = client or ClaudeClient()
    dispatch = dispatch or make_dispatch()
    tools = tools if tools is not None else tool_specs()
    return client.run(SYSTEM_PROMPT, prompt, tools, dispatch)


def _build_app():
    import typer

    app = typer.Typer(help="vike AI assistant")

    @app.command()
    def chat(prompt: str) -> None:
        """Ask the assistant to backtest/optimize/fetch/search via the engine tools."""
        typer.echo(run_chat(prompt))

    return app


def main() -> None:
    """Console-script entry point (`vike-chat`)."""
    _build_app()()


if __name__ == "__main__":
    main()
