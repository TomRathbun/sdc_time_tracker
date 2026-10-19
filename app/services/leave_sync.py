"""Sync full-day LeaveRequest approvals into DailySummary rows."""

from __future__ import annotations

from datetime import date

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
    """Clear leave hours written for this request, including a day later trimmed.

    A trimmed day is below the full target (for example 6.5 instead of 9) but
    still belongs to this request. Partial hours above the target are left alone.
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
        if (
            existing_type == leave_type_val
            and 0 < float(summary.leave_hours) <= target + 0.01
        ):
            update_daily_summary(
                db,
                leave.employee_id,
                work_date,
                leave_hours=0,
                leave_type=None,
                pto_approved=False,
            )


_TRIMMABLE = {LeaveType.vacation.value, LeaveType.sick.value, LeaveType.covid_sick.value}


def trim_week_leave(db: Session, employee_id: int, week_start: date) -> float:
    """If work plus leave is over the week target, cut the latest leave day.

    Vacation and sick only. The week target stays 40. Returns hours removed.
    """
    from datetime import timedelta

    days = []
    week_total = 0.0
    week_target = 0.0
    for offset in range(5):
        work_date = week_start + timedelta(days=offset)
        target = get_target_hours(work_date)
        week_target += target
        summary = (
            db.query(DailySummary)
            .filter(
                DailySummary.employee_id == employee_id,
                DailySummary.date == work_date,
            )
            .first()
        )
        worked = float(summary.total_hours or 0) if summary else 0.0
        leave_h = 0.0
        leave_type = None
        if summary and summary.leave_approved and summary.leave_hours:
            leave_h = float(summary.leave_hours)
            leave_type = (
                summary.leave_type.value
                if isinstance(summary.leave_type, LeaveType)
                else (str(summary.leave_type) if summary.leave_type else None)
            )
        week_total += worked + leave_h
        days.append((work_date, leave_h, leave_type))

    excess = round(week_total - week_target, 2)
    if excess <= 0.01:
        return 0.0

    removed = 0.0
    for work_date, leave_h, leave_type in reversed(days):
        if excess <= 0.01:
            break
        if leave_type not in _TRIMMABLE or leave_h <= 0:
            continue
        cut = min(leave_h, excess)
        new_hours = round(leave_h - cut, 2)
        update_daily_summary(
            db,
            employee_id,
            work_date,
            leave_hours=new_hours,
            leave_type=leave_type if new_hours > 0 else None,
            pto_approved=new_hours > 0,
        )
        excess = round(excess - cut, 2)
        removed = round(removed + cut, 2)
    return removed
