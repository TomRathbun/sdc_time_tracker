"""Unit tests for re-checkout (called-back extra session)."""

from __future__ import annotations

import unittest
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Employee, TimeEntry, EntryType, LocationType, Role
from app.auth import hash_pin
from app.services.time_state import (
    can_check_in,
    can_check_out,
    can_recheckout,
    current_status,
    plan_recheckout,
    STATUS_CHECKED_IN,
    STATUS_CHECKED_OUT,
)
from app.services.time_calc import calculate_clock_hours


class PlanRecheckoutTests(unittest.TestCase):
    def test_extra_session_from_last_out(self):
        last = datetime(2026, 9, 10, 16, 0)
        new = datetime(2026, 9, 10, 16, 45)
        pair, err = plan_recheckout(last, new)
        self.assertIsNone(err)
        self.assertEqual(pair, (last, new))

    def test_rejects_same_or_earlier_time(self):
        last = datetime(2026, 9, 10, 16, 0)
        pair, err = plan_recheckout(last, last)
        self.assertIsNone(pair)
        self.assertIn("after your last checkout", err)

        pair, err = plan_recheckout(last, datetime(2026, 9, 10, 15, 55))
        self.assertIsNone(pair)
        self.assertIsNotNone(err)


class RecheckoutStateTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        self.db = Session()
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
        self.day = date(2026, 9, 10)

    def tearDown(self):
        self.db.close()

    def _punch(self, kind: EntryType, hour: int, minute: int = 0, comments: str = ""):
        entry = TimeEntry(
            employee_id=self.emp_id,
            date=self.day,
            declared_time=datetime(2026, 9, 10, hour, minute),
            submission_time=datetime(2026, 9, 10, hour, minute),
            entry_type=kind,
            location_type=LocationType.office,
            is_remote=False,
            comments=comments,
            offset_approved=True,
        )
        self.db.add(entry)
        self.db.commit()
        return entry

    def test_normal_day_then_recheckout_pair(self):
        self._punch(EntryType.check_in, 7, 0)
        self._punch(EntryType.check_out, 16, 0)
        self.assertEqual(current_status(self.db, self.emp_id, self.day), STATUS_CHECKED_OUT)
        self.assertIsNotNone(can_check_out(self.db, self.emp_id, self.day))
        self.assertIsNone(can_check_in(self.db, self.emp_id, self.day))

        later = datetime(2026, 9, 10, 16, 45)
        self.assertIsNone(can_recheckout(self.db, self.emp_id, self.day, later))
        self.assertIsNotNone(
            can_recheckout(self.db, self.emp_id, self.day, datetime(2026, 9, 10, 16, 0))
        )

        # Simulate re-checkout punches
        self._punch(EntryType.check_in, 16, 0, comments="Return after checkout (called back)")
        self._punch(EntryType.check_out, 16, 45, comments="Re-checkout")
        self.assertEqual(current_status(self.db, self.emp_id, self.day), STATUS_CHECKED_OUT)

        entries = self.db.query(TimeEntry).filter(TimeEntry.employee_id == self.emp_id).all()
        clock = calculate_clock_hours(entries)
        # 07:00–16:00 = 9.0; 16:00–16:45 = 0.75
        self.assertEqual(clock, 9.75)

    def test_recheckout_rejected_while_still_in(self):
        self._punch(EntryType.check_in, 7, 0)
        self.assertEqual(current_status(self.db, self.emp_id, self.day), STATUS_CHECKED_IN)
        err = can_recheckout(self.db, self.emp_id, self.day, datetime(2026, 9, 10, 16, 0))
        self.assertIsNotNone(err)
        self.assertIn("still checked in", err)

    def test_return_then_second_checkout(self):
        self._punch(EntryType.check_in, 7, 0)
        self._punch(EntryType.check_out, 16, 0)
        self._punch(EntryType.check_in, 16, 20)
        self.assertEqual(current_status(self.db, self.emp_id, self.day), STATUS_CHECKED_IN)
        self.assertIsNone(
            can_check_out(self.db, self.emp_id, self.day, datetime(2026, 9, 10, 16, 45))
        )
        self._punch(EntryType.check_out, 16, 45)
        entries = self.db.query(TimeEntry).filter(TimeEntry.employee_id == self.emp_id).all()
        # 9.0 + 0.42 (16:20–16:45 = 25 min)
        self.assertEqual(calculate_clock_hours(entries), 9.42)


if __name__ == "__main__":
    unittest.main()
