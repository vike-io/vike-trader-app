"""The Strategy API — temporary re-export shim (P1 rename step).

``Strategy`` is now an alias for ``SingleSymbolStrategy`` in ``core.compat_strategy``.
All existing ``from vike_trader_app.core.strategy import Strategy`` imports continue
to work unchanged.  Phase 2 will replace this shim with the new unified Strategy class.
"""
from .compat_strategy import SingleSymbolStrategy as Strategy  # noqa: F401

__all__ = ["Strategy"]
