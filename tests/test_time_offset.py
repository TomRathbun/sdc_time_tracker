"""Time offset / variance reasons and manager reject."""

from __future__ import annotations

import unittest
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import hash_pin
from app.database import Base
from app.models import Employee, EntryType, LocationType, Role, TimeEntry
from app.services.time_calc import calculate_clock_hours
from app.services.time_offset import (
    OTHER_VARIANCE_VALUE,
    DEFAULT_VARIANCE_REASONS,
    parse_variance_reason,
    reject_time_offset,
    resolve_offset_comment,
)


class ParseVarianceReasonTests(unittest.TestCase):
    def test_canned_reason(self):
        canned = ["Forgot to punch", "Traffic / commute delay"]
        text, err = parse_variance_reason("Forgot to punch", "", canned)
        self.assertIsNone(err)
        self.assertEqual(text, "Forgot to punch")

    def test_other_requires_text(self):
        canned = ["Forgot to punch"]
        text, err = parse_variance_reason(OTHER_VARIANCE_VALUE, "", canned)
        self.assertIsNone(text)
        self.assertIn("type a reason", err)

        text, err = parse_variance_reason(OTHER_VARIANCE_VALUE, "  Doctor appointment  ", canned)
        self.assertIsNone(err)
        self.assertEqual(text, "Doctor appointment")

    def test_empty_choice(self):
        text, err = parse_variance_reason("", "", DEFAULT_VARIANCE_REASONS)
        self.assertIsNone(text)
        self.assertIn("Select a reason", err)

    def test_unknown_choice_rejected(self):
        text, err = parse_variance_reason("Not on the list", "", ["Forgot to punch"])
        self.assertIsNone(text)
        self.assertIsNotNone(err)


class RejectOffsetTests(unittest.TestCase):
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
        self.day = date(2026, 9, 10)

    def tearDown(self):
        self.db.close()

    def test_reject_reverts_declared_to_submission(self):
        declared = datetime(2026, 9, 10, 7, 0)
        actual = datetime(2026, 9, 10, 7, 45)
        entry = TimeEntry(
            employee_id=self.emp_id,
            date=self.day,
            declared_time=declared,
            submission_time=actual,
            entry_type=EntryType.check_in,
            location_type=LocationType.office,
            is_remote=False,
            comments="Forgot to punch",
            offset_approved=False,
        )
        out = TimeEntry(
            employee_id=self.emp_id,
            date=self.day,
            declared_time=datetime(2026, 9, 10, 16, 0),
            submission_time=datetime(2026, 9, 10, 16, 0),
            entry_type=EntryType.check_out,
            location_type=LocationType.office,
            is_remote=False,
            comments="",
            offset_approved=True,
        )
        self.db.add_all([entry, out])
        self.db.commit()

        before = calculate_clock_hours([entry, out])
        self.assertEqual(before, 9.0)

        info = reject_time_offset(entry)
        self.db.commit()

        self.assertEqual(entry.declared_time, actual)
        self.assertTrue(entry.offset_approved)
        self.assertEqual(info["entry_id"], entry.id)
        after = calculate_clock_hours([entry, out])
        self.assertEqual(after, 8.25)

    def test_resolve_under_threshold_keeps_optional_comment(self):
        from unittest.mock import MagicMock
        db = MagicMock()
        # Patch helpers used inside resolve_offset_comment via module-level get_setting
        from app.services import time_offset as mod
        orig_threshold = mod.get_comment_threshold_minutes
        orig_needs = mod.needs_offset_approval
        mod.get_comment_threshold_minutes = lambda _db: 30
        mod.needs_offset_approval = lambda _db, d, s: False
        try:
            text, err = resolve_offset_comment(
                db,
                datetime(2026, 9, 10, 7, 0),
                datetime(2026, 9, 10, 7, 5),
                comments="optional note",
            )
            self.assertIsNone(err)
            self.assertEqual(text, "optional note")
        finally:
            mod.get_comment_threshold_minutes = orig_threshold
            mod.needs_offset_approval = orig_needs


if __name__ == "__main__":
    unittest.main()
