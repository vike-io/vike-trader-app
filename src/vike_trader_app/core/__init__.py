"""core — the headless engine. ZERO GUI imports (no PySide6, no pyqtgraph).

Modules:
    model.py           Bar, Position, Trade (domain model)
    single_symbol_engine.py  SingleSymbolEngine — the bar event loop
    broker_sim.py            canonical cost model — fills/fees/funding (engine + kernel share it)
    strategy.py              Strategy base class (the stable API)
    strategy_loader.py       load user Strategy subclasses
    multi_symbol_engine.py   multi-symbol / cross-sectional helpers
    timeframe.py       timeframe parsing + resampling to higher TFs
    vectorized.py      fast vectorized backtest path (grid builder / parity oracle)
    fastsim.py         compiled (numba) fast_backtest kernel — parity with engine.py
    signal_strategy.py SignalStrategy front door over the compiled kernel
    paper.py           paper (forward) runner — drive the engine live, bar-at-a-time
    forward.py         back-compat shim (deprecated ForwardTester alias -> PaperTester)
    indicators/        technical-analysis library (self-describing @indicator registry)

Engine rules (implemented):
  - broker processes pending orders BEFORE the strategy runs each bar (look-ahead guard)
  - market orders fill at the NEXT bar's open
  - warm-up gating: the strategy is skipped until ``i >= Strategy.WARMUP`` (never act on NaN)

Planned (design spec, not yet implemented):
  - intrabar SL/TP: pessimistic default + count-and-flag when both fall in one bar
  - normalize gap-opens toward prior close before fills
"""
