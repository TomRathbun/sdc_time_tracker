"""Vacation and sick balances are hour banks built from work-day allowances."""

from __future__ import annotations

import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import hash_pin
from app.database import Base
from app.models import (
    DailySummary,
    Employee,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
    Role,
)
from app.services.leave_balance import (
    can_request_leave,
    entitlement_hours,
    get_leave_balance,
    partial_pto_error,
)


class LeaveHourBankTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        emp = Employee(
            name="Hour Bank",
            pin_hash=hash_pin("1234"),
            role=Role.employee,
            is_active=True,
            pin_needs_reset=False,
            vacation_days_per_year=22,
            sick_days_per_year=15,
        )
        self.db.add(emp)
        self.db.commit()
        self.emp = emp

    def tearDown(self):
        self.db.close()

    def test_defaults_are_22_and_15_work_days_in_hours(self):
        balance = get_leave_balance(self.db, self.emp, year=2026)
        self.assertEqual(balance["vacation"]["days"], 22)
        self.assertEqual(balance["vacation"]["entitlement"], entitlement_hours(22))
        self.assertEqual(balance["vacation"]["entitlement"], 176)
        self.assertEqual(balance["sick"]["days"], 15)
        self.assertEqual(balance["sick"]["entitlement"], 120)
        self.assertEqual(balance["vacation"]["remaining"], 176)
        self.assertEqual(balance["sick"]["remaining"], 120)

    def test_monday_charges_9h_friday_charges_4h(self):
        # 2026-10-05 is Monday, 2026-10-09 is Friday
        self.db.add(LeaveRequest(
            employee_id=self.emp.id,
            leave_type=LeaveType.vacation,
            start_date=date(2026, 10, 5),
            end_date=date(2026, 10, 9),
            status=LeaveStatus.pending,
        ))
        self.db.commit()
        balance = get_leave_balance(self.db, self.emp, year=2026)
        # Mon–Thu 9+9+9+9 + Fri 4 = 40h
        self.assertEqual(balance["vacation"]["pending"], 40)
        self.assertEqual(balance["vacation"]["remaining"], 136)
        self.assertEqual(balance["vacation"]["used"], 0)

    def test_approved_full_day_is_not_double_counted_with_summary(self):
        monday = date(2026, 10, 5)
        self.db.add(LeaveRequest(
            employee_id=self.emp.id,
            leave_type=LeaveType.sick,
            start_date=monday,
            end_date=monday,
            status=LeaveStatus.approved,
        ))
        self.db.add(DailySummary(
            employee_id=self.emp.id,
            date=monday,
            target_hours=9,
            leave_hours=9,
            leave_type=LeaveType.sick,
            leave_approved=True,
        ))
        self.db.commit()
        balance = get_leave_balance(self.db, self.emp, year=2026)
        self.assertEqual(balance["sick"]["used"], 9)
        self.assertEqual(balance["sick"]["remaining"], 111)

    def test_partial_pto_reserves_hours_and_blocks_overdraw(self):
        friday = date(2026, 10, 9)
        self.db.add(DailySummary(
            employee_id=self.emp.id,
            date=friday,
            target_hours=4,
            leave_hours=2,
            leave_type=LeaveType.vacation,
            leave_approved=False,
        ))
        self.db.commit()
        balance = get_leave_balance(self.db, self.emp, year=2026)
        self.assertEqual(balance["vacation"]["pending"], 2)
        self.assertEqual(balance["vacation"]["remaining"], 174)

        err = partial_pto_error(
            self.db, self.emp, "vacation", friday, new_hours=4, old_hours=2, old_type_value="vacation",
        )
        self.assertIsNone(err)

        self.emp.vacation_days_per_year = 0  # nothing left once the 2h is reserved
        self.db.commit()
        err = partial_pto_error(
            self.db, self.emp, "vacation", friday, new_hours=4, old_hours=2, old_type_value="vacation",
        )
        self.assertIsNotNone(err)
        self.assertIn("vacation hours", err)

    def test_can_request_leave_uses_hours_not_day_count(self):
        # 1 work day of allowance = 8h, but a Monday costs 9h
        self.emp.vacation_days_per_year = 1
        self.db.commit()
        err = can_request_leave(
            self.db, self.emp, LeaveType.vacation,
            date(2026, 10, 5), date(2026, 10, 5),
        )
        self.assertIsNotNone(err)
        self.assertIn("9h", err)

        err = can_request_leave(
            self.db, self.emp, LeaveType.vacation,
            date(2026, 10, 9), date(2026, 10, 9),
        )
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
