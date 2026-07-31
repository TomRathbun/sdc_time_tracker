"""Vacation / sick day balances for FOSC entitlements."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from app.config import DEFAULT_VACATION_DAYS_PER_YEAR, DEFAULT_SICK_DAYS_PER_YEAR
from app.models import Employee, LeaveRequest, LeaveStatus, LeaveType
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


def _sum_days_for_requests(
    requests: list[LeaveRequest],
    year: int,
) -> float:
    """Count workdays in year covered by leave requests (each day once)."""
    y_start, y_end = year_bounds(year)
    days: set[date] = set()
    for req in requests:
        span_start = max(req.start_date, y_start)
        span_end = min(req.end_date, y_end)
        if span_end < span_start:
            continue
        for d in iter_workdays(span_start, span_end):
            days.add(d)
    return float(len(days))


def get_leave_balance(
    db: Session,
    employee: Employee,
    year: int | None = None,
) -> dict:
    """
    Balance for vacation and sick for a calendar year.

    - Vacation entitlement: employee.vacation_days_per_year (default 30)
    - Sick entitlement: employee.sick_days_per_year (default 10)
    - Pending requests reserve days (cannot over-book)
    - Approved days count as used
    """
    if year is None:
        year = date.today().year

    vac_entitlement = float(
        employee.vacation_days_per_year
        if employee.vacation_days_per_year is not None
        else DEFAULT_VACATION_DAYS_PER_YEAR
    )
    sick_entitlement = float(
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

    vac_used = _sum_days_for_requests(vac_approved, year)
    vac_pending_days = _sum_days_for_requests(vac_pending, year)
    sick_used = _sum_days_for_requests(sick_approved, year)
    sick_pending_days = _sum_days_for_requests(sick_pending, year)

    # Remaining available to book = entitlement - used - pending (reserved)
    vac_remaining = max(0.0, vac_entitlement - vac_used - vac_pending_days)
    sick_remaining = max(0.0, sick_entitlement - sick_used - sick_pending_days)

    return {
        "year": year,
        "vacation": {
            "entitlement": vac_entitlement,
            "used": vac_used,
            "pending": vac_pending_days,
            "remaining": vac_remaining,
        },
        "sick": {
            "entitlement": sick_entitlement,
            "used": sick_used,
            "pending": sick_pending_days,
            "remaining": sick_remaining,
        },
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
    - Vacation/sick cannot exceed remaining balance (pending reserves)
    - UAE holiday has no balance cap
    """
    if end < start:
        return "End date must be on or after start date."

    workdays = count_workdays(start, end)
    if workdays <= 0:
        return "Selected range has no scheduled workdays."

    year = start.year
    # Multi-year rare; count against start year for simplicity, split if spans years
    balance = get_leave_balance(db, employee, year=year)

    if leave_type == LeaveType.vacation:
        if workdays > balance["vacation"]["remaining"] + 1e-6:
            return (
                f"Not enough vacation days. This request uses {workdays} workday(s); "
                f"you have {balance['vacation']['remaining']:.0f} remaining "
                f"(of {balance['vacation']['entitlement']:.0f})."
            )
    elif leave_type in (LeaveType.sick, LeaveType.covid_sick):
        if workdays > balance["sick"]["remaining"] + 1e-6:
            return (
                f"Not enough sick days. This request uses {workdays} workday(s); "
                f"you have {balance['sick']['remaining']:.0f} remaining "
                f"(of {balance['sick']['entitlement']:.0f})."
            )
    # uae_holiday: no entitlement cap

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
