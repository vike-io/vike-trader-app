"""Load a user-authored strategy from a .py file (dynamic import).

Finds the first ``Strategy`` subclass DEFINED in the given module (imported
subclasses are ignored) and returns the class.
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
