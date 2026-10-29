"""Injected clock: decision code never calls datetime.now() (replay determinism)."""
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Deterministic clock; each now() advances by `step` so events stay ordered."""

    def __init__(self, start: datetime | None = None, step: timedelta = timedelta(milliseconds=1)):
        self._t = start or datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
        self._step = step

    def now(self) -> datetime:
        t = self._t
        self._t = t + self._step
        return t
