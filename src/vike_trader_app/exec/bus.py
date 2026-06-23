"""A Qt-free, re-entrant in-process event router for exec events.

Shaped like ``ui/linkbus.SymbolLinkBus`` (one process-local router, duck-typed handlers, no Qt) but
with the OPPOSITE re-entrancy semantic: where SymbolLinkBus *suppresses* a nested broadcast (correct
to stop symbol-link ping-pong), this bus *defers and delivers* it. A handler reacting to one event by
publishing another (an OCO sibling-cancel on a fill, a RiskGate auto-cancel on an over-limit book) must
have that event delivered to every subscriber — so a nested ``publish`` enqueues and the outer drain
loop processes it FIFO after the current fan-out. Single-threaded; Qt provides thread-marshalling in
later phases (handlers run on the main thread).
"""

from __future__ import annotations

from collections import deque
from typing import Callable


class EventBus:
    """Fan-out router. ``subscribe`` a callable(event); ``publish`` delivers to all, re-entrantly."""

    def __init__(self) -> None:
        self._subscribers: list[Callable] = []
        self._queue: deque = deque()
        self._draining = False

    def subscribe(self, handler: Callable) -> None:
        if handler not in self._subscribers:
            self._subscribers.append(handler)

    def unsubscribe(self, handler: Callable) -> None:
        if handler in self._subscribers:
            self._subscribers.remove(handler)

    def publish(self, event) -> None:
        """Deliver ``event`` to every subscriber; nested publishes are queued and drained FIFO."""
        self._queue.append(event)
        if self._draining:
            return
        self._draining = True
        try:
            while self._queue:
                ev = self._queue.popleft()
                for handler in list(self._subscribers):
                    handler(ev)
        finally:
            self._draining = False
