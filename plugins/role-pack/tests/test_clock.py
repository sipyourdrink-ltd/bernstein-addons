from __future__ import annotations

import re
import time
import unittest
from datetime import UTC, datetime, timedelta

from bernstein_role_pack.clock import (
    Clock,
    ClockError,
    NaiveDeadline,
    Reading,
    StaleReading,
    deadline_state,
    parse_deadline,
)


class ClockTests(unittest.TestCase):
    def test_stamp_is_utc_z(self) -> None:
        self.assertRegex(Clock("UTC", 60).stamp(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_local_has_numeric_offset(self) -> None:
        clock = Clock("Asia/Tokyo", 60)
        reading = clock.read()
        local = clock.local(reading)
        self.assertTrue(local.endswith("+09:00"), local)
        self.assertEqual(datetime.fromisoformat(local), reading.utc.replace(microsecond=0))

    def test_naive_deadline_refused(self) -> None:
        for text in ("2026-12-01T10:00:00", "2026-12-01"):
            with self.assertRaises(NaiveDeadline):
                parse_deadline(text)

    def test_garbage_deadline_refused(self) -> None:
        with self.assertRaises(ClockError):
            parse_deadline("next Tuesday")

    def test_offset_deadline_stored_as_utc(self) -> None:
        self.assertEqual(parse_deadline("2026-12-01T12:00:00+02:00"), datetime(2026, 12, 1, 10, tzinfo=UTC))
        self.assertEqual(parse_deadline("2026-12-01T10:00:00Z"), datetime(2026, 12, 1, 10, tzinfo=UTC))

    def test_stale_reading_refused(self) -> None:
        stale = Reading(datetime.now(UTC), time.monotonic() - 120, 60)
        with self.assertRaises(StaleReading):
            stale.utc
        with self.assertRaises(StaleReading):
            Clock("UTC", 60).stamp(stale)
        with self.assertRaises(StaleReading):
            deadline_state(datetime.now(UTC), stale, 3600)

    def test_deadline_states(self) -> None:
        reading = Clock("UTC", 60).read()
        now = reading.utc
        self.assertEqual(deadline_state(now - timedelta(seconds=1), reading, 3600), "overdue")
        self.assertEqual(deadline_state(now + timedelta(minutes=30), reading, 3600), "due_now")
        self.assertEqual(deadline_state(now + timedelta(hours=2), reading, 3600), "future")

    def test_stamp_has_no_offset_text(self) -> None:
        self.assertIsNone(re.search(r"[+-]\d{2}:\d{2}$", Clock("Asia/Tokyo", 60).stamp()))


if __name__ == "__main__":
    unittest.main()
