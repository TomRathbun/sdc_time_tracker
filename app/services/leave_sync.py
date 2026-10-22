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


def trim_historical_leave(db: Session) -> float:
    """Apply the extra-hours leave reduction to every finished week.

    Runs once. Later weeks are handled when the weekly summary is sent.
    """
    from datetime import timedelta

    from app.models import Employee, Role
    from app.services.settings import get_setting, set_setting

    if get_setting(db, "leave_trim_retro") == "done":
        return 0.0

    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    this_friday = this_monday + timedelta(days=4)
    last_monday = this_monday if this_friday < today else this_monday - timedelta(days=7)

    people = (
        db.query(Employee)
        .filter(Employee.is_active == True, Employee.role != Role.customer)  # noqa: E712
        .all()
    )
    removed = 0.0
    for person in people:
        first = (
            db.query(DailySummary.date)
            .filter(
                DailySummary.employee_id == person.id,
                DailySummary.leave_approved == True,  # noqa: E712
                DailySummary.leave_hours > 0,
            )
            .order_by(DailySummary.date)
            .first()
        )
        if not first:
            continue
        monday = first[0] - timedelta(days=first[0].weekday())
        while monday <= last_monday:
            for offset in range(5):
                day = monday + timedelta(days=offset)
                existing = (
                    db.query(DailySummary)
                    .filter(DailySummary.employee_id == person.id, DailySummary.date == day)
                    .first()
                )
                if existing:
                    update_daily_summary(db, person.id, day)
            removed = round(removed + trim_week_leave(db, person.id, monday), 2)
            monday += timedelta(days=7)

    set_setting(db, "leave_trim_retro", "done")
    return removed


def recalc_beod_floor(db: Session) -> int:
    """Apply the 5-hour BEOD floor to every claimed day. Runs once."""
    from datetime import timedelta

    from app.services.settings import get_setting, set_setting

    if get_setting(db, "beod_floor_5h") == "done":
        return 0

    rows = (
        db.query(DailySummary)
        .filter(DailySummary.lunch_end_of_day == True)  # noqa: E712
        .all()
    )
    claimed = [(row.employee_id, row.date, float(row.beod_hours or 0)) for row in rows]
    gained = 0
    weeks = set()
    for employee_id, work_date, before in claimed:
        update_daily_summary(db, employee_id, work_date)
        after = (
            db.query(DailySummary)
            .filter(DailySummary.employee_id == employee_id, DailySummary.date == work_date)
            .first()
        )
        if after and float(after.beod_hours or 0) > before + 0.001:
            gained += 1
        if work_date.weekday() < 5:
            monday = work_date - timedelta(days=work_date.weekday())
            weeks.add((employee_id, monday))

    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    this_friday = this_monday + timedelta(days=4)
    last_monday = this_monday if this_friday < today else this_monday - timedelta(days=7)
    for employee_id, monday in weeks:
        if monday <= last_monday:
            trim_week_leave(db, employee_id, monday)

    set_setting(db, "beod_floor_5h", "done")
    return gained
