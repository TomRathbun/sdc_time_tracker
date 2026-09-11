"""BEOD checkbox and +1h credit share one manager-configured hours gate."""

from __future__ import annotations

import unittest
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Employee, TimeEntry, EntryType, LocationType, Role
from app.auth import hash_pin
from app.config import BEOD_MINIMUM_HOURS
from app.services.settings import seed_settings, set_setting, get_beod_minimum_hours
from app.services.time_calc import show_beod_option, update_daily_summary


class BeodOptionTests(unittest.TestCase):
    def test_fallback_default_is_five(self):
        self.assertEqual(BEOD_MINIMUM_HOURS, 5.0)

    def test_hidden_below_gate(self):
        self.assertFalse(show_beod_option(4.99, min_hours=5.0))
        self.assertFalse(show_beod_option(5.0, min_hours=6.0))

    def test_shown_at_gate(self):
        self.assertTrue(show_beod_option(5.0, min_hours=5.0))
        self.assertTrue(show_beod_option(6.0, min_hours=6.0))

    def test_hidden_if_already_claimed(self):
        self.assertFalse(show_beod_option(8.0, already_claimed=True, min_hours=5.0))


class BeodSettingTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        seed_settings(self.db)
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

    def _session(self, start_h, end_h):
        ci = TimeEntry(
            employee_id=self.emp_id,
            date=self.day,
            declared_time=datetime(2026, 9, 11, start_h, 0),
            submission_time=datetime(2026, 9, 11, start_h, 0),
            entry_type=EntryType.check_in,
            location_type=LocationType.office,
            is_remote=False,
            comments="",
            offset_approved=True,
        )
        co = TimeEntry(
            employee_id=self.emp_id,
            date=self.day,
            declared_time=datetime(2026, 9, 11, end_h, 0),
            submission_time=datetime(2026, 9, 11, end_h, 0),
            entry_type=EntryType.check_out,
            location_type=LocationType.office,
            is_remote=False,
            comments="",
            offset_approved=True,
        )
        self.db.add_all([ci, co])
        self.db.commit()

    def test_default_setting_is_five(self):
        self.assertEqual(get_beod_minimum_hours(self.db), 5.0)

    def test_manager_override(self):
        set_setting(self.db, "beod_minimum_hours", "6.5")
        self.assertEqual(get_beod_minimum_hours(self.db), 6.5)

    def test_credit_at_five_hours_with_default(self):
        self._session(7, 12)  # 5.0h
        summary = update_daily_summary(
            self.db, self.emp_id, self.day,
            lunch_end_of_day=True, lunch_approved=True,
        )
        self.assertEqual(summary.beod_hours, 1.0)
        self.assertEqual(summary.total_hours, 6.0)

    def test_no_credit_below_gate(self):
        self._session(7, 11)  # 4.0h
        summary = update_daily_summary(
            self.db, self.emp_id, self.day,
            lunch_end_of_day=True, lunch_approved=True,
        )
        self.assertEqual(summary.beod_hours, 0.0)
        self.assertEqual(summary.total_hours, 4.0)

    def test_raising_gate_blocks_credit(self):
        set_setting(self.db, "beod_minimum_hours", "6")
        self._session(7, 12)  # 5.0h
        summary = update_daily_summary(
            self.db, self.emp_id, self.day,
            lunch_end_of_day=True, lunch_approved=True,
        )
        self.assertEqual(summary.beod_hours, 0.0)
        self.assertEqual(summary.total_hours, 5.0)
        self.assertFalse(show_beod_option(5.0, min_hours=get_beod_minimum_hours(self.db)))


if __name__ == "__main__":
    unittest.main()
