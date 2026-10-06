"""Vacation / sick balances for FOSC entitlements, tracked in hours."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from app.config import (
    DEFAULT_SICK_DAYS_PER_YEAR,
    DEFAULT_VACATION_DAYS_PER_YEAR,
    LEAVE_HOURS_PER_WORK_DAY,
)
from app.models import DailySummary, Employee, LeaveRequest, LeaveStatus, LeaveType
from app.services.time_calc import get_target_hours


def iter_workdays(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        if get_target_hours(d) > 0:
            yield d
        d += timedelta(days=1)


def count_workdays(start: date, end: date) -> int:
    return sum(1 for _ in iter_workdays(start, end))


def year_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def _types_for_bucket(bucket: str) -> list[LeaveType]:
    if bucket == "vacation":
        return [LeaveType.vacation]
    if bucket == "sick":
        return [LeaveType.sick, LeaveType.covid_sick]
    return []


def format_hours(value: float) -> str:
    """176.0 → '176', 4.5 → '4.5'."""
    rounded = round(float(value) + 0.0, 2)
    if abs(rounded - round(rounded)) < 0.001:
        return str(int(round(rounded)))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def entitlement_hours(days: float) -> float:
    """Work-day allowance → hour bank. One work day = 8h (40h week / 5 days)."""
    return round(float(days) * LEAVE_HOURS_PER_WORK_DAY, 2)


def hours_for_workdays(start: date, end: date) -> float:
    """Charge each workday at that day's FOSC target (9h Mon–Thu, 4h Friday)."""
    if end < start:
        return 0.0
    return round(sum(get_target_hours(d) for d in iter_workdays(start, end)), 2)


def _hours_for_requests(
    requests: list[LeaveRequest],
    year: int,
) -> tuple[float, set[date]]:
    """Sum target hours of workdays in year. Each date counts once."""
    y_start, y_end = year_bounds(year)
    seen: set[date] = set()
    hours = 0.0
    for req in requests:
        span_start = max(req.start_date, y_start)
        span_end = min(req.end_date, y_end)
        if span_end < span_start:
            continue
        for d in iter_workdays(span_start, span_end):
            if d in seen:
                continue
            seen.add(d)
            hours += get_target_hours(d)
    return round(hours, 2), seen


def _partial_hours(
    db: Session,
    employee_id: int,
    year: int,
    types: list[LeaveType],
    covered: set[date],
    *,
    approved: bool,
) -> float:
    """Partial PTO hours not already reserved by a full-day leave request."""
    if not types:
        return 0.0
    y_start, y_end = year_bounds(year)
    rows = (
        db.query(DailySummary)
        .filter(
            DailySummary.employee_id == employee_id,
            DailySummary.date >= y_start,
            DailySummary.date <= y_end,
            DailySummary.leave_hours > 0,
            DailySummary.leave_type.in_(types),
            DailySummary.leave_approved == approved,  # noqa: E712
        )
        .all()
    )
    total = 0.0
    for row in rows:
        if row.date in covered:
            continue
        total += float(row.leave_hours or 0)
    return round(total, 2)


def _bucket_balance(
    db: Session,
    employee: Employee,
    year: int,
    *,
    days: float,
    approved: list[LeaveRequest],
    pending: list[LeaveRequest],
    types: list[LeaveType],
) -> dict:
    entitlement = entitlement_hours(days)
    used_req, covered_used = _hours_for_requests(approved, year)
    pending_req, covered_pending = _hours_for_requests(pending, year)
    covered = covered_used | covered_pending
    used = round(used_req + _partial_hours(
        db, employee.id, year, types, covered, approved=True,
    ), 2)
    pending_hours = round(pending_req + _partial_hours(
        db, employee.id, year, types, covered, approved=False,
    ), 2)
    remaining = round(max(0.0, entitlement - used - pending_hours), 2)
    return {
        "days": float(days),
        "entitlement_days": float(days),
        "hours_per_day": LEAVE_HOURS_PER_WORK_DAY,
        "entitlement": entitlement,
        "used": used,
        "pending": pending_hours,
        "remaining": remaining,
    }


def get_leave_balance(
    db: Session,
    employee: Employee,
    year: int | None = None,
) -> dict:
    """
    Hour bank for vacation and sick for a calendar year.

    - Vacation allowance: employee.vacation_days_per_year work days (default 22)
      → 22 × 8h = 176h
    - Sick allowance: employee.sick_days_per_year work days (default 15)
      → 15 × 8h = 120h
    - A full day taken charges that weekday's target (9h Mon–Thu, 4h Friday)
    - Partial PTO charges the hours entered
    - Pending requests reserve hours
    """
    if year is None:
        year = date.today().year

    vac_days = float(
        employee.vacation_days_per_year
        if employee.vacation_days_per_year is not None
        else DEFAULT_VACATION_DAYS_PER_YEAR
    )
    sick_days = float(
        employee.sick_days_per_year
        if employee.sick_days_per_year is not None
        else DEFAULT_SICK_DAYS_PER_YEAR
    )

    y_start, y_end = year_bounds(year)

    all_leave = (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.employee_id == employee.id,
            LeaveRequest.status.in_([LeaveStatus.pending, LeaveStatus.approved]),
            LeaveRequest.start_date <= y_end,
            LeaveRequest.end_date >= y_start,
        )
        .all()
    )

    vac_approved = [r for r in all_leave if r.leave_type == LeaveType.vacation and r.status == LeaveStatus.approved]
    vac_pending = [r for r in all_leave if r.leave_type == LeaveType.vacation and r.status == LeaveStatus.pending]
    sick_types = {LeaveType.sick, LeaveType.covid_sick}
    sick_approved = [r for r in all_leave if r.leave_type in sick_types and r.status == LeaveStatus.approved]
    sick_pending = [r for r in all_leave if r.leave_type in sick_types and r.status == LeaveStatus.pending]

    return {
        "year": year,
        "hours_per_day": LEAVE_HOURS_PER_WORK_DAY,
        "vacation": _bucket_balance(
            db, employee, year,
            days=vac_days,
            approved=vac_approved,
            pending=vac_pending,
            types=[LeaveType.vacation],
        ),
        "sick": _bucket_balance(
            db, employee, year,
            days=sick_days,
            approved=sick_approved,
            pending=sick_pending,
            types=[LeaveType.sick, LeaveType.covid_sick],
        ),
    }


def can_request_leave(
    db: Session,
    employee: Employee,
    leave_type: LeaveType,
    start: date,
    end: date,
    exclude_request_id: int | None = None,
) -> str | None:
    """
    Validate a new leave request. Returns error message or None if OK.

    - Workdays must be > 0
    - Vacation/sick cannot exceed remaining hours (pending reserves)
    - UAE holiday has no balance cap
    """
    if end < start:
        return "End date must be on or after start date."

    workdays = count_workdays(start, end)
    if workdays <= 0:
        return "Selected range has no scheduled workdays."

    hours = hours_for_workdays(start, end)
    year = start.year
    balance = get_leave_balance(db, employee, year=year)

    if leave_type == LeaveType.vacation:
        bucket = balance["vacation"]
        label = "vacation"
    elif leave_type in (LeaveType.sick, LeaveType.covid_sick):
        bucket = balance["sick"]
        label = "sick"
    else:
        bucket = None
        label = ""

    if bucket is not None and hours > bucket["remaining"] + 1e-6:
        return (
            f"Not enough {label} hours. This request uses {format_hours(hours)}h "
            f"({workdays} workday(s) at the day's target); "
            f"you have {format_hours(bucket['remaining'])}h remaining "
            f"of {format_hours(bucket['entitlement'])}h "
            f"({format_hours(bucket['days'])} work days)."
        )

    # Overlap with existing pending/approved leave
    existing = (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.employee_id == employee.id,
            LeaveRequest.status.in_([LeaveStatus.pending, LeaveStatus.approved]),
            LeaveRequest.start_date <= end,
            LeaveRequest.end_date >= start,
        )
        .all()
    )
    for req in existing:
        if exclude_request_id and req.id == exclude_request_id:
            continue
        return (
            f"Overlaps existing {req.leave_type.value.replace('_', ' ')} leave "
            f"({req.start_date} → {req.end_date})."
        )

    return None


def partial_pto_error(
    db: Session,
    employee: Employee,
    leave_type_value: str,
    work_date: date,
    new_hours: float,
    old_hours: float = 0.0,
    old_type_value: str | None = None,
) -> str | None:
    """Refuse a partial PTO edit that would overdraw the hour bank.

    Hours already saved on this date for the same type are already in the
    balance, so only the increase is checked.
    """
    if new_hours <= 0 or leave_type_value not in ("vacation", "sick"):
        return None
    balance = get_leave_balance(db, employee, year=work_date.year)
    bucket_name = "sick" if leave_type_value == "sick" else "vacation"
    old_bucket = (
        "sick" if old_type_value == "sick"
        else "vacation" if old_type_value == "vacation"
        else None
    )
    old_same = float(old_hours or 0) if old_bucket == bucket_name else 0.0
    extra = max(0.0, float(new_hours) - old_same)
    bucket = balance[bucket_name]
    if extra > bucket["remaining"] + 1e-6:
        return (
            f"Not enough {bucket_name} hours. This adds {format_hours(extra)}h; "
            f"you have {format_hours(bucket['remaining'])}h remaining "
            f"of {format_hours(bucket['entitlement'])}h "
            f"({format_hours(bucket['days'])} work days)."
        )
    return None
