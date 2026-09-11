"""Projected checkout time: show after check-in, clear after checkout or new day."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import hash_pin
from app.database import Base
from app.models import Employee, EntryType, LocationType, Role, TimeEntry
from app.services.time_calc import (
    beod_offered_on,
    get_target_hours,
    hours_to_meet_target,
    projected_checkout_from_entries,
    projected_checkout_time,
)


class ProjectedCheckoutTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        emp = Employee(
            name="Test User",
            pin_hash=hash_pin("1234"),
            role=Role.employee,
            is_active=True,
            pin_needs_reset=False,
        )
        self.db.add(emp)
        self.db.commit()
        self.emp_id = emp.id
        # Monday — 9h target
        self.monday = date(2026, 9, 7)
        # Friday — 4h target
        self.friday = date(2026, 9, 11)
        # Saturday — no target
        self.saturday = date(2026, 9, 12)

    def tearDown(self):
        self.db.close()

    def _punch(self, kind, day, hour, minute):
        entry = TimeEntry(
            employee_id=self.emp_id,
            date=day,
            declared_time=datetime(day.year, day.month, day.day, hour, minute),
            submission_time=datetime(day.year, day.month, day.day, hour, minute),
            entry_type=kind,
            location_type=LocationType.office,
            is_remote=False,
            comments="",
            offset_approved=True,
        )
        self.db.add(entry)
        self.db.commit()
        return entry

    def _entries(self, day):
        return (
            self.db.query(TimeEntry)
            .filter(TimeEntry.employee_id == self.emp_id, TimeEntry.date == day)
            .order_by(TimeEntry.declared_time)
            .all()
        )

    def test_weekday_checkin_plus_nine_hours(self):
        checkin = datetime(2026, 9, 7, 7, 0)
        proj = projected_checkout_time(checkin, self.monday)
        self.assertEqual(proj, datetime(2026, 9, 7, 16, 0))
        self.assertEqual(get_target_hours(self.monday), 9.0)
        self.assertEqual(hours_to_meet_target(self.monday), 9.0)
        self.assertEqual(hours_to_meet_target(self.monday, beod=True), 8.0)
        self.assertTrue(beod_offered_on(self.monday))

    def test_weekday_beod_is_eight_hours(self):
        checkin = datetime(2026, 9, 7, 7, 0)
        proj = projected_checkout_time(checkin, self.monday, beod=True)
        self.assertEqual(proj, datetime(2026, 9, 7, 15, 0))

    def test_friday_checkin_plus_four_hours(self):
        checkin = datetime(2026, 9, 11, 7, 0)
        proj = projected_checkout_time(checkin, self.friday)
        self.assertEqual(proj, datetime(2026, 9, 11, 11, 0))
        self.assertEqual(hours_to_meet_target(self.friday), 4.0)
        # Friday is below the 6h BEOD floor, so BEOD does not shorten the day
        self.assertEqual(hours_to_meet_target(self.friday, beod=True), 4.0)
        self.assertFalse(beod_offered_on(self.friday))
        self.assertFalse(beod_offered_on(self.saturday))
        self.assertEqual(
            projected_checkout_time(checkin, self.friday, beod=True),
            datetime(2026, 9, 11, 11, 0),
        )

    def test_weekend_has_no_projection(self):
        checkin = datetime(2026, 9, 12, 8, 0)
        self.assertIsNone(projected_checkout_time(checkin, self.saturday))

    def test_from_entries_after_checkin_includes_beod(self):
        self._punch(EntryType.check_in, self.monday, 7, 0)
        proj = projected_checkout_from_entries(self._entries(self.monday), self.monday)
        self.assertEqual(proj.standard, datetime(2026, 9, 7, 16, 0))
        self.assertEqual(proj.beod, datetime(2026, 9, 7, 15, 0))
        self.assertFalse(proj.beod_claimed)

    def test_friday_from_entries_has_no_beod_variant(self):
        self._punch(EntryType.check_in, self.friday, 7, 0)
        proj = projected_checkout_from_entries(self._entries(self.friday), self.friday)
        self.assertEqual(proj.standard, datetime(2026, 9, 11, 11, 0))
        self.assertIsNone(proj.beod)

    def test_cleared_after_checkout(self):
        self._punch(EntryType.check_in, self.monday, 7, 0)
        self._punch(EntryType.check_out, self.monday, 16, 0)
        proj = projected_checkout_from_entries(self._entries(self.monday), self.monday)
        self.assertIsNone(proj.standard)
        self.assertIsNone(proj.beod)

    def test_cleared_after_midnight_new_work_date(self):
        self._punch(EntryType.check_in, self.monday, 7, 0)
        next_day = self.monday + timedelta(days=1)
        proj = projected_checkout_from_entries(self._entries(next_day), next_day)
        self.assertIsNone(proj.standard)
        self.assertIsNone(proj.beod)

    def test_return_session_uses_remaining_hours(self):
        # 5h already worked; 4h remaining on a 9h day, 3h remaining with BEOD
        self._punch(EntryType.check_in, self.monday, 7, 0)
        self._punch(EntryType.check_out, self.monday, 12, 0)
        self._punch(EntryType.check_in, self.monday, 13, 0)
        proj = projected_checkout_from_entries(self._entries(self.monday), self.monday)
        self.assertEqual(proj.standard, datetime(2026, 9, 7, 17, 0))
        self.assertEqual(proj.beod, datetime(2026, 9, 7, 16, 0))

    def test_no_projection_when_target_already_met(self):
        self._punch(EntryType.check_in, self.monday, 7, 0)
        self._punch(EntryType.check_out, self.monday, 16, 0)
        self._punch(EntryType.check_in, self.monday, 16, 30)
        proj = projected_checkout_from_entries(self._entries(self.monday), self.monday)
        self.assertIsNone(proj.standard)
        self.assertIsNone(proj.beod)

    def test_claimed_beod_shows_eight_hour_time_only(self):
        self._punch(EntryType.check_in, self.monday, 7, 0)
        proj = projected_checkout_from_entries(
            self._entries(self.monday), self.monday, beod_claimed=True,
        )
        self.assertEqual(proj.standard, datetime(2026, 9, 7, 15, 0))
        self.assertIsNone(proj.beod)
        self.assertTrue(proj.beod_claimed)


if __name__ == "__main__":
    unittest.main()
