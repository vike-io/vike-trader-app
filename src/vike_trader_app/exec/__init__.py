"""Live-execution event spine: immutable events, the order-status FSM, the bus, the ledger.

Qt-free by rule — nothing here imports PySide6. The live ``ExecutionClient``/``OmsHub`` (later
phases) build on these primitives. See ``docs/research/2026-06-23-order-event-architecture.md``.
"""
