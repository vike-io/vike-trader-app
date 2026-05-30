"""core — the headless engine. ZERO GUI imports (no PySide6, no pyqtgraph).

Modules (created during implementation, test-first):
    model.py        Bar, Order, Trade, Position (domain model)
    engine.py       BacktestEngine — the bar event loop
    strategy.py     Strategy base class + StrategyContext (the stable API)
    broker_sim.py   simulated fills: fees, funding, intrabar rules, look-ahead guards
    forwardtest.py  forward-test runner (Phase 3)
    indicators/     technical-analysis library

Engine rules (validated against MT5 / Jesse / Backtrader / backtesting.py source):
  - broker processes pending orders BEFORE the strategy runs each bar (look-ahead guard)
  - market orders fill at the NEXT bar's open
  - intrabar SL/TP: pessimistic default + count-and-flag when both fall in one bar
  - normalize gap-opens toward prior close before fills
  - gate the strategy on an indicator warm-up count (never act on NaN)
"""
