"""vike-trader-app — a Python-native, crypto-first backtesting & forward-testing platform.

A modern MT4/MT5-style tool: a visual desktop backtester + a headless CLI sharing one
engine, with a built-in anti-overfitting suite as the differentiator. No live execution.

Architecture (see docs/superpowers/specs/2026-05-29-vike-trader-app-backtesting-platform-design.md):

    ui/, cli/   -> presentation (thin faces over the engine)
    analysis/   -> optimizer + anti-overfitting validation suite (the differentiator)
    core/       -> engine, strategy API, domain model, indicators   (NO ui imports)
    data/       -> data access (Parquet/Polars, Binance, websocket) + SQLite metadata

GOLDEN RULE: core/ must never import ui/. The engine always runs headless.
"""

__version__ = "0.0.0"
