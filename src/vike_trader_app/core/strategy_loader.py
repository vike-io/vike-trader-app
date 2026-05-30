"""Load a user-authored strategy from a .py file (dynamic import).

Finds the first ``Strategy`` subclass DEFINED in the given module (imported
subclasses are ignored) and returns the class.
"""

import importlib.util
import inspect

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
