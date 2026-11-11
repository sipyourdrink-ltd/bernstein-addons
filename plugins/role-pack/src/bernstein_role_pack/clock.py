"""Clock policy: one zone for display, UTC for storage, fresh readings only."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from bernstein_role_pack.config import RolePackError


class ClockError(RolePackError):
    """Base class for clock policy refusals."""


class StaleReading(ClockError):
    """A reading was used after its maximum age."""


class NaiveDeadline(ClockError):
    """A deadline was given without an explicit offset."""


@dataclass(frozen=True)
class Reading:
    """A wall-clock reading paired with the monotonic mark it was taken at."""

    taken_utc: datetime
    mark: float
    max_age_s: float

    @property
    def utc(self) -> datetime:
        age = time.monotonic() - self.mark
        if age > self.max_age_s:
            raise StaleReading(f"clock reading is {age:.1f}s old, limit is {self.max_age_s:g}s")
        return self.taken_utc


class Clock:
    def __init__(self, zone: str, max_reading_age_s: float) -> None:
        self.zone = ZoneInfo(zone)
        self.max_reading_age_s = max_reading_age_s

    def read(self) -> Reading:
        return Reading(datetime.now(UTC), time.monotonic(), self.max_reading_age_s)

    def stamp(self, reading: Reading | None = None) -> str:
        """UTC timestamp, YYYY-MM-DDTHH:MM:SSZ, from a fresh reading by default."""
        return format_utc((reading or self.read()).utc)

    def local(self, reading: Reading | None = None) -> str:
        """The reading in the configured zone with its numeric offset."""
        return (reading or self.read()).utc.astimezone(self.zone).isoformat(timespec="seconds")


def format_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_deadline(text: str) -> datetime:
    """Parse a timestamp that carries an explicit offset or Z; return it in UTC."""
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        raise ClockError(f"deadline {text!r} is not an ISO 8601 timestamp") from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise NaiveDeadline(f"deadline {text!r} has no offset; write Z or +HH:MM")
    return moment.astimezone(UTC)


def deadline_state(deadline: datetime, reading: Reading, due_window_s: float) -> str:
    now = reading.utc
    if deadline < now:
        return "overdue"
    if deadline - now <= timedelta(seconds=due_window_s):
        return "due_now"
    return "future"
