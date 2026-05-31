"""Back-compat shim. ``ForwardTester`` was renamed ``PaperTester`` (moved to ``core/paper.py``);
"forward" is reserved for walk-forward. Import from ``core.paper`` in new code.
"""

from .paper import PaperTester, pump

ForwardTester = PaperTester  # deprecated alias

__all__ = ["PaperTester", "ForwardTester", "pump"]
