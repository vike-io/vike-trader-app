"""AI services — the real engine surface (validate / backtest / walk-forward / optimize / discovery).

Complements ``tests/unit/core/test_ai_services.py`` (which covers the low-level SMA/overfit helpers).
Here we exercise the higher-level surface an MCP/Claude-Code agent actually drives:
``validate_strategy``, ``run_backtest``, ``run_walk_forward``, ``run_optimization`` (which load
real cached BTCUSDT Parquet via the Catalog), plus the discovery endpoints.

No network, no Qt: bars come from the local Parquet cache on the calling thread (safe — these are
pure single-threaded functions, exactly the headless MCP-server use case).
"""

from pathlib import Path

import pytest

from vike_trader_app.ai.services import (
    list_indicators,
    list_scanner_rules,
    list_strategy_templates,
    run_backtest,
    run_optimization,
    run_walk_forward,
    validate_strategy,
)
from vike_trader_app.data.catalog import Catalog

# A complete, preflight-clean strategy that trades on the cached data, with a PARAM_GRID so the
# optimize/walk-forward smoke tests have a grid to sweep. Uses the no-super().__init__ pattern the
# sandbox AST gate requires.
_GOOD = """from vike_trader_app.core.strategy import SingleSymbolStrategy as Strategy


class S(Strategy):
    WARMUP = 30
    fast = 10
    slow = 30
    PARAM_GRID = {"fast": [5, 10], "slow": [20, 30]}

    def __init__(self):
        self.closes = []

    def on_bar(self, bar):
        self.closes.append(bar.close)
        if len(self.closes) <= self.slow:
            return
        f = sum(self.closes[-self.fast:]) / self.fast
        s = sum(self.closes[-self.slow:]) / self.slow
        fp = sum(self.closes[-self.fast - 1:-1]) / self.fast
        sp = sum(self.closes[-self.slow - 1:-1]) / self.slow
        if fp <= sp and f > s and self.position.size == 0:
            self.buy(1.0)
        elif fp >= sp and f < s and self.position.size > 0:
            self.close()
"""

# Forbidden import — the AST safety gate must reject this without executing it.
_BAD_IMPORT = "import os\n" + _GOOD


def _find_data_root() -> str | None:
    """Locate a ``storage/parquet`` tree that has cached BTCUSDT 1m bars.

    The test may run from a git worktree whose own ``storage/parquet`` is empty, so we probe the
    cwd, this file's repo, and the parent (main) checkout. Returns the first root with data, else
    None (caller skips).
    """
    here = Path(__file__).resolve()
    candidates = [Path.cwd() / "storage" / "parquet"]
    for parent in here.parents:
        candidates.append(parent / "storage" / "parquet")
        # If we're inside a worktree (…/.claude/worktrees/<id>/…), the main checkout is up the tree.
    seen = set()
    for root in candidates:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        try:
            if Catalog(str(root)).query("BTCUSDT", "1m", None, None):
                return str(root)
        except Exception:
            continue
    return None


@pytest.fixture(scope="module")
def data_root() -> str:
    root = _find_data_root()
    if root is None:
        pytest.skip("no cached BTCUSDT 1m Parquet found — run fetch_ohlcv to populate the cache")
    return root


@pytest.fixture(scope="module")
def window(data_root) -> tuple[int, int]:
    """A small, recent [start, end] slice (~600 bars) so optimize/WF stay fast but trade."""
    bars = Catalog(data_root).query("BTCUSDT", "1m", None, None)
    sub = bars[-600:]
    return sub[0].ts, sub[-1].ts


# --- validate_strategy -----------------------------------------------------

def test_validate_strategy_good_is_ok():
    out = validate_strategy(_GOOD)
    assert out["ok"] is True
    assert out["problems"] == []


def test_validate_strategy_bad_import_is_rejected_with_problems():
    out = validate_strategy(_BAD_IMPORT)
    assert out["ok"] is False
    assert out["problems"]  # non-empty list of AST-gate reasons
    # the offending import surfaces in the human-readable reasons
    assert any("os" in p or "import" in p.lower() for p in out["problems"])


# --- run_backtest ----------------------------------------------------------

def test_run_backtest_returns_report_dict(data_root, window):
    start_ms, end_ms = window
    out = run_backtest(_GOOD, "BTCUSDT", "1m", start_ms, end_ms,
                       config={"taker_fee": 0.0}, root=data_root)
    # standardized TesterReport headline metrics + the symbol/interval/n_bars stamps
    assert out["symbol"] == "BTCUSDT"
    assert out["interval"] == "1m"
    assert out["n_bars"] >= 100
    for key in ("sharpe", "n_bars", "n_trades", "total_return", "max_drawdown", "final_equity"):
        assert key in out, f"missing report key {key!r}"
    assert isinstance(out["n_trades"], int)


def test_run_backtest_honors_params_override(data_root, window):
    start_ms, end_ms = window
    out = run_backtest(_GOOD, "BTCUSDT", "1m", start_ms, end_ms,
                       params={"fast": 5, "slow": 20}, config={"taker_fee": 0.0}, root=data_root)
    assert "sharpe" in out and out["n_bars"] >= 100


def test_run_backtest_missing_data_raises(data_root):
    # A symbol that isn't cached must fail honestly with a clear message.
    with pytest.raises(ValueError) as exc:
        run_backtest(_GOOD, "NOPE_NOT_CACHED", "1m", root=data_root)
    assert "no cached bars" in str(exc.value).lower()


# --- run_walk_forward ------------------------------------------------------

def test_run_walk_forward_smoke(data_root, window):
    start_ms, end_ms = window
    out = run_walk_forward(_GOOD, "BTCUSDT", "1m", start_ms, end_ms,
                           n_splits=3, criterion="sharpe", mode="anchored",
                           config={"taker_fee": 0.0}, root=data_root)
    assert out["n_windows"] == 3
    assert out["mode"] == "anchored"
    assert out["criterion"] == "sharpe"
    assert "wf_efficiency" in out and "wf_consistency" in out
    # stitched OOS report headline + an overfit verdict (level + reasons) attached
    assert "oos" in out and "sharpe" in out["oos"]
    assert len(out["windows"]) == 3
    for w in out["windows"]:
        assert "best_params" in w and "is_score" in w and "oos_score" in w


# --- run_optimization ------------------------------------------------------

def test_run_optimization_smoke(data_root, window):
    start_ms, end_ms = window
    out = run_optimization(_GOOD, "BTCUSDT", "1m", start_ms, end_ms,
                           criterion="sharpe", method="grid", top_n=4,
                           config={"taker_fee": 0.0}, root=data_root)
    assert out["criterion"] == "sharpe"
    assert out["n_trials"] >= 1          # 2x2 grid = 4 combos
    assert out["effective_n"] >= 1
    assert set(out["best"]["params"]) == {"fast", "slow"}
    assert "metrics" in out["best"] and "sharpe" in out["best"]["metrics"]
    assert 1 <= len(out["ranked"]) <= 4


def test_run_optimization_without_grid_raises(data_root, window):
    start_ms, end_ms = window
    no_grid = _GOOD.replace('    PARAM_GRID = {"fast": [5, 10], "slow": [20, 30]}\n', "")
    with pytest.raises(ValueError) as exc:
        run_optimization(no_grid, "BTCUSDT", "1m", start_ms, end_ms, root=data_root)
    assert "param_grid" in str(exc.value).lower() or "grid" in str(exc.value).lower()


# --- discovery -------------------------------------------------------------

def test_list_strategy_templates_non_empty():
    out = list_strategy_templates()
    assert out["n"] >= 1
    assert len(out["templates"]) == out["n"]
    first = out["templates"][0]
    assert {"name", "category", "description", "code"} <= set(first)
    assert "class" in first["code"]  # full runnable source


def test_list_scanner_rules_non_empty():
    out = list_scanner_rules()
    assert out["n"] >= 1
    assert len(out["rules"]) == out["n"]
    assert all({"name", "description", "type"} <= set(r) for r in out["rules"])
    assert any(r["type"] == "base" for r in out["rules"])


def test_list_indicators_non_empty():
    out = list_indicators()
    assert out["n"] >= 1
    names = {ind["name"] for ind in out["indicators"]}
    assert {"rsi", "macd", "sma"} & names  # at least one well-known indicator present
