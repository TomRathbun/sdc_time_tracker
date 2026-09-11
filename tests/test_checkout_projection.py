"""Projected checkout times after check-in, with and without BEOD."""

from __future__ import annotations

import unittest
from datetime import datetime

from app.services.time_calc import project_checkout_times, round_up_5min


class RoundUpTests(unittest.TestCase):
    def test_already_on_mark(self):
        dt = datetime(2026, 9, 11, 16, 0)
        self.assertEqual(round_up_5min(dt), dt)

    def test_rounds_up(self):
        dt = datetime(2026, 9, 11, 15, 16, 40)
        self.assertEqual(round_up_5min(dt), datetime(2026, 9, 11, 15, 20))


class ProjectionTests(unittest.TestCase):
    def test_weekday_nine_hours(self):
        ci = datetime(2026, 9, 10, 7, 0)  # Thursday
        proj = project_checkout_times(ci, target_hours=9.0)
        self.assertEqual(proj["without_display"], "16:00")
        self.assertEqual(proj["with_beod_display"], "15:00")

    def test_friday_four_hours(self):
        ci = datetime(2026, 9, 11, 7, 0)
        proj = project_checkout_times(ci, target_hours=4.0)
        self.assertEqual(proj["without_display"], "11:00")
        self.assertEqual(proj["with_beod_display"], "10:00")

    def test_return_session_uses_remaining(self):
        # 07:00–12:00 already done (5h), back at 13:00, target 9
        ci = datetime(2026, 9, 10, 13, 0)
        proj = project_checkout_times(ci, target_hours=9.0, completed_clock_hours=5.0)
        self.assertEqual(proj["without_display"], "17:00")
        self.assertEqual(proj["with_beod_display"], "16:00")

    def test_offsite_reduces_remaining(self):
        ci = datetime(2026, 9, 10, 7, 0)
        proj = project_checkout_times(ci, target_hours=9.0, extra_hours=1.0)
        self.assertEqual(proj["without_display"], "15:00")
        self.assertEqual(proj["with_beod_display"], "14:00")

    def test_none_when_no_target(self):
        self.assertIsNone(project_checkout_times(datetime(2026, 9, 12, 9, 0), target_hours=0))

    def test_rounds_projected_time_up(self):
        ci = datetime(2026, 9, 10, 7, 2)
        proj = project_checkout_times(ci, target_hours=9.0)
        self.assertEqual(proj["without_display"], "16:05")
        self.assertEqual(proj["with_beod_display"], "15:05")


if __name__ == "__main__":
    unittest.main()
