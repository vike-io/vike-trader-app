"""Ask-the-AI symbol suggester for the New DataSet flow.

Reuses the project's LLM client (``ai.llm.ClaudeClient``, behind the ``[ai]`` extra) with no tools,
then parses the reply into a clean symbol list via ``data.datasets.parse_symbols``.
"""

from ..data.datasets import parse_symbols
from .llm import ClaudeClient

_SYSTEM = (
    "You suggest ticker symbols for a backtesting DataSet. Reply with ONLY the symbols, "
    "separated by spaces or commas — no prose, no numbering, no explanation. "
    "Use exchange-native tickers (e.g. BTCUSDT for Binance crypto, EURUSD for FX)."
)


def suggest_symbols(query: str, *, client=None, group: str | None = None) -> list[str]:
    """Return a parsed symbol list for a natural-language ``query``.

    ``client`` is an ``LLMClient`` (injectable for tests); if None, a ``ClaudeClient`` is built
    (raises a friendly ImportError when the ``[ai]`` extra/API key is missing). ``group`` (e.g.
    'Binance' / 'Dukascopy') is woven into the system prompt to bias the symbol format.
    """
    system = _SYSTEM if not group else f"{_SYSTEM} Prefer symbols suitable for the {group} provider."
    llm = client if client is not None else ClaudeClient()
    reply = llm.run(system, query, [], lambda *_a, **_k: {})
    lines = [ln for ln in reply.splitlines() if ln.strip()]
    return parse_symbols(lines[-1]) if lines else []
