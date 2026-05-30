"""Reproducible result bundles: fingerprint the data and write a re-runnable artifact.

A bundle directory contains everything needed to reproduce or audit a run:
the strategy source, params, config (incl. data hash + seed), headline metrics,
and the full trade log. Stdlib only.
"""

import csv
import hashlib
import json
import shutil
from pathlib import Path

from . import metrics


def data_hash(bars) -> str:
    """Deterministic SHA-256 hex digest of an OHLCV bar series (order-sensitive)."""
    h = hashlib.sha256()
    for b in bars:
        h.update(f"{b.ts}:{b.open}:{b.high}:{b.low}:{b.close}:{b.volume}\n".encode())
    return h.hexdigest()


def save_bundle(
    out_dir,
    *,
    result,
    strategy_source: str | None = None,
    strategy_path: str | None = None,
    params: dict | None = None,
    config: dict | None = None,
    bars=None,
    seed: int | None = None,
) -> Path:
    """Write a self-contained, re-runnable bundle to ``out_dir`` and return its path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # strategy source (explicit string wins; else copy the file)
    if strategy_source is not None:
        (out / "strategy.py").write_text(strategy_source)
    elif strategy_path is not None:
        shutil.copyfile(strategy_path, out / "strategy.py")

    (out / "params.json").write_text(json.dumps(params or {}, indent=2))

    cfg = dict(config or {})
    cfg["seed"] = seed
    if bars:
        cfg["data_hash"] = data_hash(bars)
        cfg["n_bars"] = len(bars)
        cfg["start_ts"] = bars[0].ts
        cfg["end_ts"] = bars[-1].ts
    (out / "config.json").write_text(json.dumps(cfg, indent=2))

    eq = result.equity_curve
    (out / "metrics.json").write_text(
        json.dumps(
            {
                "total_return": metrics.total_return(eq),
                "final_equity": result.final_equity,
                "n_trades": len(result.trades),
                "win_rate": metrics.win_rate(result.trades),
                "profit_factor": metrics.profit_factor(result.trades),
                "max_drawdown": metrics.max_drawdown(eq),
                "sharpe": metrics.sharpe(eq),
            },
            indent=2,
        )
    )

    with (out / "trades.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entry_price", "exit_price", "size", "pnl", "fees", "entry_ts", "exit_ts"])
        for t in result.trades:
            w.writerow([t.entry_price, t.exit_price, t.size, t.pnl, t.fees, t.entry_ts, t.exit_ts])

    return out
