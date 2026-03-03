"""Time calculation service — 9/4 schedule, lunch rules, compliance."""

from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import WEEKDAY_HOURS
from app.models import TimeEntry, OffsiteEntry, DailySummary, EntryType


def get_target_hours(work_date: date) -> float:
    """Get the target hours for a given weekday."""
    return float(WEEKDAY_HOURS.get(work_date.weekday(), 0))


def calculate_daily_hours(
    time_entries: List[TimeEntry],
    offsite_entries: List[OffsiteEntry],
) -> float:
    """
    Calculate total worked hours for a day.

    Pairs check-in/check-out entries and sums the durations.
    Adds offsite entry durations as well.
    Lunch is included in clock time (paid 1-hour lunch).
    """
    total_seconds = 0.0

    # Sort time entries by declared_time
    sorted_entries = sorted(time_entries, key=lambda e: e.declared_time)

    # Pair check-in with the next check-out
    pending_checkin: Optional[TimeEntry] = None
    for entry in sorted_entries:
        if entry.entry_type == EntryType.check_in:
            pending_checkin = entry
        elif entry.entry_type == EntryType.check_out and pending_checkin:
            delta = entry.declared_time - pending_checkin.declared_time
            total_seconds += delta.total_seconds()
            pending_checkin = None

    # Add offsite entry durations
    for offsite in offsite_entries:
        delta = offsite.end_time - offsite.start_time
        total_seconds += delta.total_seconds()

    return round(total_seconds / 3600.0, 2)


def check_compliance(total_hours: float, target_hours: float) -> bool:
    """Check if total hours meet the target (with small tolerance)."""
    return total_hours >= (target_hours - 0.01)


def update_daily_summary(
    db: Session,
    employee_id: int,
    work_date: date,
    lunch_end_of_day: bool = False,
    lunch_approved: bool = False,
    leave_hours: float = -1.0,
    leave_type: str | None = None,
    pto_approved: bool = False,
) -> DailySummary:
    """Recalculate and update/create the daily summary for an employee.

    Business rules:
    - Lunch EOD bonus (+1h) requires both approval AND raw worked >= 6h
    - PTO hours only count toward compliance when leave_approved=True
    - Compliance: total_hours + (leave_hours if approved) >= target
    - leave_hours < 0 means "don't change existing PTO"
    - leave_hours == 0 means "clear PTO"
    """
    # Get all time entries for this employee on this date
    time_entries = db.query(TimeEntry).filter(
        TimeEntry.employee_id == employee_id,
        TimeEntry.date == work_date,
    ).all()

    offsite_entries = db.query(OffsiteEntry).filter(
        OffsiteEntry.employee_id == employee_id,
        OffsiteEntry.date == work_date,
    ).all()

    raw_hours = calculate_daily_hours(time_entries, offsite_entries)
    target_hours = get_target_hours(work_date)

    # Find or create summary (need existing flags)
    summary = db.query(DailySummary).filter(
        DailySummary.employee_id == employee_id,
        DailySummary.date == work_date,
    ).first()

    # Merge lunch flags: once set, stay set (OR logic)
    eff_lunch_eod = lunch_end_of_day or (summary.lunch_end_of_day if summary else False)
    eff_lunch_approved = lunch_approved or (summary.lunch_approved if summary else False)

    # PTO fields — leave_hours < 0 means "preserve existing"
    if leave_hours >= 0:
        eff_leave_hours = leave_hours
        eff_leave_type = leave_type
        # If hours set to 0, clear PTO entirely
        if leave_hours == 0:
            eff_leave_type = None
            eff_leave_approved_flag = False
        else:
            # New/updated PTO resets approval (manager must re-approve)
            eff_leave_approved_flag = pto_approved
    else:
        eff_leave_hours = summary.leave_hours if summary else 0.0
        eff_leave_type = summary.leave_type if summary else None
        eff_leave_approved_flag = (summary.leave_approved if summary else False) or pto_approved

    # Lunch bonus: only if approved AND raw worked hours >= 6
    LUNCH_MINIMUM_HOURS = 6.0
    total_hours = raw_hours
    if eff_lunch_eod and eff_lunch_approved and raw_hours >= LUNCH_MINIMUM_HOURS:
        total_hours = round(raw_hours + 1.0, 2)

    # Compliance: worked + approved PTO >= target
    approved_leave = eff_leave_hours if eff_leave_approved_flag else 0.0
    effective_total = total_hours + approved_leave
    is_compliant = check_compliance(effective_total, target_hours)

    if summary:
        summary.total_hours = total_hours
        summary.leave_hours = eff_leave_hours
        summary.leave_type = eff_leave_type
        summary.leave_approved = eff_leave_approved_flag
        summary.target_hours = target_hours
        summary.is_compliant = is_compliant
        summary.lunch_end_of_day = eff_lunch_eod
        summary.lunch_approved = eff_lunch_approved
    else:
        summary = DailySummary(
            employee_id=employee_id,
            date=work_date,
            total_hours=total_hours,
            leave_hours=eff_leave_hours,
            leave_type=eff_leave_type,
            leave_approved=eff_leave_approved_flag,
            target_hours=target_hours,
            is_compliant=is_compliant,
            lunch_end_of_day=eff_lunch_eod,
            lunch_approved=eff_lunch_approved,
        )
        db.add(summary)

    db.commit()
    db.refresh(summary)
    return summary


def get_weekly_summary(
    db: Session,
    employee_id: int,
    week_start: date,
) -> dict:
    """Get a weekly summary (Mon-Fri) for dashboard display."""
    days = []
    total_worked = 0.0
    total_target = 0.0

    for i in range(5):  # Mon to Fri
        day = week_start + timedelta(days=i)
        summary = db.query(DailySummary).filter(
            DailySummary.employee_id == employee_id,
            DailySummary.date == day,
        ).first()

        target = get_target_hours(day)
        worked = summary.total_hours if summary else 0.0
        compliant = summary.is_compliant if summary else False

        days.append({
            "date": day,
            "day_name": day.strftime("%A"),
            "worked": worked,
            "target": target,
            "compliant": compliant,
        })
        total_worked += worked
        total_target += target

    return {
        "days": days,
        "total_worked": round(total_worked, 2),
        "total_target": total_target,
        "week_compliant": total_worked >= (total_target - 0.01),
    }
