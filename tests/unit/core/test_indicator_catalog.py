"""The indicator catalogue: every snippet is preflight-clean, runs, and computes correctly."""

from vike_trader_app.analysis.indicator_catalog import CATALOG, Indicator
from vike_trader_app.core.sandbox.preflight import check_strategy_source


def _load(name):
    ind = next(i for i in CATALOG if i.name == name)
    ns: dict = {}
    exec(ind.snippet, ns)  # noqa: S102 - trusted in-repo catalogue strings
    return ns


def test_catalog_nonempty():
    assert len(CATALOG) >= 8
    assert all(isinstance(i, Indicator) and i.snippet and i.name for i in CATALOG)


def test_every_snippet_is_preflight_clean():
    # inserting any snippet must never trip the sandbox AST gate
    for ind in CATALOG:
        assert check_strategy_source(ind.snippet) == [], (ind.name, check_strategy_source(ind.snippet))


def test_every_snippet_execs_without_error():
    for ind in CATALOG:
        exec(ind.snippet, {})  # noqa: S102 - catches syntax / import errors in the catalogue


def test_sma_and_rsi_values():
    assert _load("SMA")["sma"]([1, 2, 3, 4], 2) == 3.5
    rsi = _load("RSI")["rsi"]
    assert rsi(list(range(1, 40)), 14) == 100.0   # all-gains series -> RSI maxes out
    assert rsi(list(range(40, 1, -1)), 14) == 0.0  # all-losses series -> RSI bottoms out
