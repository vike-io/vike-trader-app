"""Load a user-authored strategy from a .py file (dynamic import).

Finds the first ``Strategy`` subclass DEFINED in the given module (imported
subclasses are ignored) and returns the class.  Use ``load_any_strategy_from_string``
when the code may contain either a ``Strategy`` or a ``PortfolioStrategy`` (A2d live
portfolio routing).
"""

import importlib.util
import inspect
import os
import tempfile

from .strategy import Strategy


def load_strategy_from_file(path: str) -> type[Strategy]:
    """Import ``path`` and return its Strategy subclass. Raises ValueError if none."""
    spec = importlib.util.spec_from_file_location("vike_trader_app_user_strategy", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot import strategy module from {path!r}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    candidates = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, Strategy) and obj is not Strategy and obj.__module__ == module.__name__
    ]
    if not candidates:
        raise ValueError(f"no Strategy subclass found in {path!r}")
    return candidates[0]


def load_strategy_from_string(code: str, *, validate: bool = True) -> type[Strategy]:
    """Load a Strategy subclass from source TEXT.

    With ``validate`` (default), runs the AST pre-flight gate first and raises ``ValueError`` on any
    problem. Materializes ``code`` to a temp ``.py`` and reuses ``load_strategy_from_file``. This is
    NOT a sandbox — run untrusted code via ``core.sandbox.run_sandboxed``.
    """
    if validate:
        from .sandbox.preflight import check_strategy_source

        problems = check_strategy_source(code)
        if problems:
            raise ValueError("strategy source rejected by pre-flight: " + "; ".join(problems))
    fd, path = tempfile.mkstemp(suffix=".py", prefix="vike_strategy_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        return load_strategy_from_file(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def load_any_strategy_from_string(code: str, *, validate: bool = True) -> type:
    """Load a ``Strategy`` OR ``PortfolioStrategy`` subclass from source TEXT (A2d).

    Extends ``load_strategy_from_string`` to also accept ``PortfolioStrategy`` subclasses
    (which do NOT inherit from ``Strategy``).  Used by the live-portfolio routing path in the
    UI so a single ``_on_run_live_requested`` can handle both single-symbol and portfolio code.

    Returns the class — callers check ``issubclass(cls, PortfolioStrategy)`` to decide which
    live pump to start.
    """
    from .portfolio import PortfolioStrategy
    if validate:
        from .sandbox.preflight import check_strategy_source
        problems = check_strategy_source(code)
        if problems:
            raise ValueError("strategy source rejected by pre-flight: " + "; ".join(problems))
    fd, path = tempfile.mkstemp(suffix=".py", prefix="vike_strategy_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        spec = importlib.util.spec_from_file_location("vike_trader_app_user_strategy", path)
        if spec is None or spec.loader is None:
            raise ValueError(f"cannot import strategy module from {path!r}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Prefer Strategy subclasses first; fall back to PortfolioStrategy subclasses.
        candidates: list[type] = [
            obj
            for _, obj in inspect.getmembers(module, inspect.isclass)
            if obj.__module__ == module.__name__
            and (
                (issubclass(obj, Strategy) and obj is not Strategy)
                or (issubclass(obj, PortfolioStrategy) and obj is not PortfolioStrategy)
            )
        ]
        if not candidates:
            raise ValueError(f"no Strategy or PortfolioStrategy subclass found in {path!r}")
        return candidates[0]
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
