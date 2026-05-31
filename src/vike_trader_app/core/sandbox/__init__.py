"""sandbox — safe execution of AI-generated strategy source.

``preflight.check_strategy_source`` is a fast in-process AST gate (input hygiene + editor
diagnostics). The actual security boundary is the out-of-process runner (added next): a
separate, hard-killable child process with a wall-clock timeout. Heavy imports stay lazy so
importing ``sandbox.preflight`` is cheap.
"""
