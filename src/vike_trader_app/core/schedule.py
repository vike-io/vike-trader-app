"""Scheduled strategy callbacks fired at bar boundaries (QC-style Schedule.On).

A ``DateRule`` answers "should this fire at this bar?"; a ``Schedule`` holds (rule, callback) pairs
and, each bar, returns the callbacks whose rule is due — firing each rule at most once per bar.
The engine consults it right after ``on_bar`` (after fills), so it runs on the deterministic
single-threaded loop.

Bar-based only (UTC ``bar.ts``) — no wall-clock / time-of-day / timer-thread scheduling.
"""

from __future__ import annotations

from collections.abc import Callable


class DateRule:
    """Base: return True when a scheduled callback should fire at this bar."""

    def is_due(self, ts_ms: int, bar_index: int) -> bool:  # noqa: ARG002
        raise NotImplementedError


class PeriodStart(DateRule):
    """Fire on the first bar of each new calendar period (monthly/weekly/quarterly/yearly/daily)."""

    def __init__(self, period: str) -> None:
        self.period = period
        self._last_key: str | None = None

    def is_due(self, ts_ms: int, bar_index: int) -> bool:  # noqa: ARG002
        # Lazy import to avoid core -> analysis import cycle
        # (analysis.* already imports from core.*)
        from ..analysis.periods import period_key  # noqa: PLC0415

        key = period_key(ts_ms, self.period)
        if key == self._last_key:
            return False
        self._last_key = key
        return True


class EveryNBars(DateRule):
    """Fire every ``n`` bars (when ``bar_index % n == 0``)."""

    def __init__(self, n: int) -> None:
        if n < 1:
            raise ValueError("EveryNBars(n) requires n >= 1")
        self.n = n

    def is_due(self, ts_ms: int, bar_index: int) -> bool:  # noqa: ARG002
        return bar_index % self.n == 0


def MonthStart() -> PeriodStart:  # noqa: N802 - QC-style factory name
    return PeriodStart("monthly")


def WeekStart() -> PeriodStart:  # noqa: N802
    return PeriodStart("weekly")


def QuarterStart() -> PeriodStart:  # noqa: N802
    return PeriodStart("quarterly")


def YearStart() -> PeriodStart:  # noqa: N802
    return PeriodStart("yearly")


class Schedule:
    """Registry of (rule, callback) pairs, consulted once per bar by the engine."""

    def __init__(self) -> None:
        self._rules: list[tuple[DateRule, Callable[[], None]]] = []
        self._last_fired: dict[int, int] = {}  # id(rule) -> last bar_index fired

    def on(self, rule: DateRule, callback: Callable[[], None]) -> None:
        """Register ``callback`` to fire (no args) whenever ``rule.is_due`` is True."""
        self._rules.append((rule, callback))

    def check_due(self, ts_ms: int, bar_index: int) -> list[Callable[[], None]]:
        """Return callbacks due at this bar, firing each rule at most once per ``bar_index``."""
        due: list[Callable[[], None]] = []
        for rule, cb in self._rules:
            rid = id(rule)
            if self._last_fired.get(rid) == bar_index:
                continue
            if rule.is_due(ts_ms, bar_index):
                self._last_fired[rid] = bar_index
                due.append(cb)
        return due
