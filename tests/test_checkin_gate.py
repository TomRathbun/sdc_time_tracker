"""Check-in is allowed only when not currently in (kiosk button rule)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Employee, TimeEntry, EntryType, LocationType, Role
from app.auth import hash_pin
from app.services.time_state import can_check_in, current_status, STATUS_CHECKED_IN, STATUS_NOT_STARTED


class CheckinGateTests(unittest.TestCase):
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
        self.day = date(2026, 9, 11)

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

    def test_allowed_before_any_punch(self):
        self.assertEqual(current_status(self.db, self.emp_id, self.day), STATUS_NOT_STARTED)
        self.assertIsNone(can_check_in(self.db, self.emp_id, self.day))

    def test_blocked_while_checked_in(self):
        self._punch(EntryType.check_in, self.day, 7, 0)
        self.assertEqual(current_status(self.db, self.emp_id, self.day), STATUS_CHECKED_IN)
        err = can_check_in(self.db, self.emp_id, self.day)
        self.assertIsNotNone(err)
        self.assertIn("already checked in", err)

    def test_allowed_again_after_checkout(self):
        self._punch(EntryType.check_in, self.day, 7, 0)
        self._punch(EntryType.check_out, self.day, 16, 0)
        self.assertIsNone(can_check_in(self.db, self.emp_id, self.day))

    def test_allowed_after_midnight_new_work_date(self):
        self._punch(EntryType.check_in, self.day, 7, 0)
        self.assertIsNotNone(can_check_in(self.db, self.emp_id, self.day))
        next_day = self.day + timedelta(days=1)
        self.assertEqual(current_status(self.db, self.emp_id, next_day), STATUS_NOT_STARTED)
        self.assertIsNone(can_check_in(self.db, self.emp_id, next_day))


if __name__ == "__main__":
    unittest.main()
