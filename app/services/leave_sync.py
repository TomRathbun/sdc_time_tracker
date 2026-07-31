"""Sync full-day LeaveRequest approvals into DailySummary rows."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import DailySummary, LeaveRequest, LeaveType
from app.services.leave_balance import iter_workdays
from app.services.time_calc import get_target_hours, update_daily_summary


def apply_leave_approval(db: Session, leave: LeaveRequest) -> None:
    """Write full-day leave hours onto DailySummary for each workday in range.

    Full vacation day = that day's FOSC target (9h Mon–Thu, 4h Fri).
    """
    leave_type_val = (
        leave.leave_type.value if isinstance(leave.leave_type, LeaveType) else str(leave.leave_type)
    )
    for work_date in iter_workdays(leave.start_date, leave.end_date):
        target = get_target_hours(work_date)
        update_daily_summary(
            db,
            leave.employee_id,
            work_date,
            leave_hours=target,
            leave_type=leave_type_val,
            pto_approved=True,
        )


def clear_full_day_leave_for_request(db: Session, leave: LeaveRequest) -> None:
    """
    Clear full-day leave hours that match this request's type/target.

    Used when rejecting (defensive) or if leave is later un-approved.
    Skips days where leave_hours look like partial PTO (not equal to target).
    """
    leave_type_val = (
        leave.leave_type.value if isinstance(leave.leave_type, LeaveType) else str(leave.leave_type)
    )
    for work_date in iter_workdays(leave.start_date, leave.end_date):
        target = get_target_hours(work_date)
        summary = (
            db.query(DailySummary)
            .filter(
                DailySummary.employee_id == leave.employee_id,
                DailySummary.date == work_date,
            )
            .first()
        )
        if not summary or not summary.leave_hours:
            continue

        existing_type = (
            summary.leave_type.value
            if isinstance(summary.leave_type, LeaveType)
            else (str(summary.leave_type) if summary.leave_type else None)
        )
        # Only clear when it looks like full-day leave of the same type
        if (
            abs(summary.leave_hours - target) < 0.01
            and existing_type == leave_type_val
        ):
            update_daily_summary(
                db,
                leave.employee_id,
                work_date,
                leave_hours=0,
                leave_type=None,
                pto_approved=False,
            )
