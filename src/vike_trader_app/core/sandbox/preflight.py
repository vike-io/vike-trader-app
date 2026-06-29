"""AST pre-flight gate. INPUT HYGIENE + editor squiggles — NOT a security boundary.

Rejects obviously-dangerous source (forbidden imports/names, dunder attribute access) before it
reaches the loader. Normal control flow (while/for/if) is allowed — runaway loops are caught by
the sandbox timeout, not here. Untrusted code must still be run via ``core.sandbox.run_sandboxed``.
"""

import ast

_ALLOWED_IMPORTS = {
    "math", "statistics", "datetime", "numpy",
    "vike_trader_app.core.strategy", "vike_trader_app.core.model", "vike_trader_app.core.indicators",
    "vike_trader_app.core.multi_symbol_engine",  # PortfolioStrategy base class (A2d live portfolio)
    "vike_trader_app.core.compat_strategy",  # SingleSymbolStrategy compat shim (single-symbol API)
}
_FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "__import__", "open", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "input", "os", "sys", "subprocess", "socket",
    "shutil", "pathlib", "builtins",
    # __builtins__ (bare name) is the dict-bypass to __import__/eval — deny it alongside the dunder
    # ATTRIBUTE rule below (which already blocks ().__class__... chains).
    "__builtins__",
    # Interactive / process-control builtins: breakpoint() drops into pdb (arbitrary code + a hang on
    # the headless/MCP path); exit()/quit() kill the host; help()/copyright/license/credits can block
    # on a pager. None belong in a strategy. (This is HYGIENE/defence-in-depth — the real boundary is
    # still the out-of-process run_sandboxed; see the module docstring.)
    "breakpoint", "help", "exit", "quit", "copyright", "license", "credits",
}


def _root(module: str) -> str:
    return (module or "").split(".")[0]


def _allowed(module: str) -> bool:
    return module in _ALLOWED_IMPORTS or _root(module) in _ALLOWED_IMPORTS


def check_strategy_source(code: str) -> list[str]:
    """Human-readable problems with ``code`` (empty list == passed the gate)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error: {e.msg} (line {e.lineno})"]

    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if not _allowed(a.name):
                    problems.append(f"import not allowed: {a.name} (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom):
            if not _allowed(node.module or ""):
                problems.append(f"import not allowed: from {node.module} (line {node.lineno})")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                problems.append(f"dunder attribute access not allowed: {node.attr} (line {node.lineno})")
        elif isinstance(node, ast.Name):
            if node.id in _FORBIDDEN_NAMES:
                problems.append(f"name not allowed: {node.id} (line {node.lineno})")
    return problems
