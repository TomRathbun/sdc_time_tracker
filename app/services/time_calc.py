"""Time calculation service — FOSC schedule, BEOD, phone/offsite rollup."""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from app.config import WEEKDAY_HOURS, BEOD_MINIMUM_HOURS
from app.models import (
    TimeEntry, OffsiteEntry, PhoneSupportEntry, DailySummary, EntryType, LeaveType,
)


def get_target_hours(work_date: date) -> float:
    """Get the FOSC target hours for a given weekday (9 Mon–Thu, 4 Fri)."""
    return float(WEEKDAY_HOURS.get(work_date.weekday(), 0))


def calculate_clock_hours(time_entries: List[TimeEntry]) -> float:
    """Sum paired check-in / check-out durations (hours)."""
    total_seconds = 0.0
    sorted_entries = sorted(time_entries, key=lambda e: e.declared_time)
    pending_checkin: Optional[TimeEntry] = None
    for entry in sorted_entries:
        if entry.entry_type == EntryType.check_in:
            pending_checkin = entry
        elif entry.entry_type == EntryType.check_out and pending_checkin:
            delta = entry.declared_time - pending_checkin.declared_time
            total_seconds += delta.total_seconds()
            pending_checkin = None
    return round(total_seconds / 3600.0, 2)


def calculate_offsite_hours(offsite_entries: List[OffsiteEntry]) -> float:
    total_seconds = 0.0
    for offsite in offsite_entries:
        delta = offsite.end_time - offsite.start_time
        total_seconds += delta.total_seconds()
    return round(total_seconds / 3600.0, 2)


def calculate_phone_hours(phone_entries: List[PhoneSupportEntry]) -> float:
    return round(sum(p.hours or 0.0 for p in phone_entries), 2)


def calculate_daily_hours(
    time_entries: List[TimeEntry],
    offsite_entries: List[OffsiteEntry],
    phone_entries: Optional[List[PhoneSupportEntry]] = None,
) -> float:
    """
    FOSC Normal Time base (before BEOD credit):
    clock + offsite + phone support.
    Paid lunch on a normal day is already inside the In→Out window.
    """
    clock = calculate_clock_hours(time_entries)
    offsite = calculate_offsite_hours(offsite_entries)
    phone = calculate_phone_hours(phone_entries or [])
    return round(clock + offsite + phone, 2)


def check_compliance(total_hours: float, target_hours: float) -> bool:
    """Check if total hours meet the target (with small tolerance)."""
    return total_hours >= (target_hours - 0.01)


def _coerce_leave_type(leave_type) -> LeaveType | None:
    if leave_type is None or leave_type == "":
        return None
    if isinstance(leave_type, LeaveType):
        return leave_type
    try:
        return LeaveType(str(leave_type))
    except ValueError:
        return None


def update_daily_summary(
    db: Session,
    employee_id: int,
    work_date: date,
    lunch_end_of_day: bool = False,
    lunch_approved: bool = False,
    leave_hours: float = -1.0,
    leave_type: str | LeaveType | None = None,
    pto_approved: bool = False,
) -> DailySummary:
    """Recalculate and update/create the daily summary for an employee.

    FOSC Normal Time = clock + offsite + phone + BEOD credit (+1 if claimed,
    approved, and work hours before credit >= BEOD_MINIMUM_HOURS).

    Leave hours only count toward compliance when leave_approved=True.
    leave_hours < 0 means preserve existing leave fields.
    leave_hours == 0 clears leave.
    """
    time_entries = db.query(TimeEntry).filter(
        TimeEntry.employee_id == employee_id,
        TimeEntry.date == work_date,
    ).all()

    offsite_entries = db.query(OffsiteEntry).filter(
        OffsiteEntry.employee_id == employee_id,
        OffsiteEntry.date == work_date,
    ).all()

    phone_entries = db.query(PhoneSupportEntry).filter(
        PhoneSupportEntry.employee_id == employee_id,
        PhoneSupportEntry.date == work_date,
    ).all()

    clock_hours = calculate_clock_hours(time_entries)
    offsite_hours = calculate_offsite_hours(offsite_entries)
    phone_hours = calculate_phone_hours(phone_entries)
    # Work hours that count toward the BEOD 6h floor (not leave)
    work_hours = round(clock_hours + offsite_hours + phone_hours, 2)
    target_hours = get_target_hours(work_date)

    summary = db.query(DailySummary).filter(
        DailySummary.employee_id == employee_id,
        DailySummary.date == work_date,
    ).first()

    # BEOD flags: once claimed, stay claimed (OR); approval can be set by blanket or manager
    eff_beod_claimed = lunch_end_of_day or (summary.lunch_end_of_day if summary else False)
    eff_beod_approved = lunch_approved or (summary.lunch_approved if summary else False)

    if leave_hours >= 0:
        eff_leave_hours = leave_hours
        eff_leave_type = _coerce_leave_type(leave_type)
        if leave_hours == 0:
            eff_leave_type = None
            eff_leave_approved_flag = False
        else:
            eff_leave_approved_flag = pto_approved
    else:
        eff_leave_hours = summary.leave_hours if summary else 0.0
        eff_leave_type = summary.leave_type if summary else None
        eff_leave_approved_flag = (summary.leave_approved if summary else False) or pto_approved

    beod_hours = 0.0
    total_hours = work_hours
    if eff_beod_claimed and eff_beod_approved and work_hours >= BEOD_MINIMUM_HOURS:
        beod_hours = 1.0
        total_hours = round(work_hours + 1.0, 2)
    elif eff_beod_claimed and work_hours < BEOD_MINIMUM_HOURS:
        # Claimed but below floor — no credit; keep claim flag for audit/display
        beod_hours = 0.0

    approved_leave = eff_leave_hours if eff_leave_approved_flag else 0.0
    effective_total = total_hours + approved_leave
    is_compliant = check_compliance(effective_total, target_hours)

    if summary:
        summary.total_hours = total_hours
        summary.clock_hours = clock_hours
        summary.offsite_hours = offsite_hours
        summary.phone_hours = phone_hours
        summary.beod_hours = beod_hours
        summary.leave_hours = eff_leave_hours
        summary.leave_type = eff_leave_type
        summary.leave_approved = eff_leave_approved_flag
        summary.target_hours = target_hours
        summary.is_compliant = is_compliant
        summary.lunch_end_of_day = eff_beod_claimed
        summary.lunch_approved = eff_beod_approved
    else:
        summary = DailySummary(
            employee_id=employee_id,
            date=work_date,
            total_hours=total_hours,
            clock_hours=clock_hours,
            offsite_hours=offsite_hours,
            phone_hours=phone_hours,
            beod_hours=beod_hours,
            leave_hours=eff_leave_hours,
            leave_type=eff_leave_type,
            leave_approved=eff_leave_approved_flag,
            target_hours=target_hours,
            is_compliant=is_compliant,
            lunch_end_of_day=eff_beod_claimed,
            lunch_approved=eff_beod_approved,
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
    """Weekly summary (Mon–Fri). Effective hours include approved leave."""
    days = []
    total_worked = 0.0
    total_target = 0.0

    for i in range(5):
        day = week_start + timedelta(days=i)
        summary = db.query(DailySummary).filter(
            DailySummary.employee_id == employee_id,
            DailySummary.date == day,
        ).first()

        target = get_target_hours(day)
        worked = summary.total_hours if summary else 0.0
        leave_hrs = 0.0
        if summary and summary.leave_approved and summary.leave_hours:
            leave_hrs = summary.leave_hours
        effective = round(worked + leave_hrs, 2)
        compliant = summary.is_compliant if summary else False

        days.append({
            "date": day,
            "day_name": day.strftime("%A"),
            "worked": worked,
            "leave_hours": leave_hrs,
            "effective": effective,
            "target": target,
            "compliant": compliant,
            "clock_hours": summary.clock_hours if summary else 0.0,
            "phone_hours": summary.phone_hours if summary else 0.0,
            "offsite_hours": summary.offsite_hours if summary else 0.0,
            "beod_hours": summary.beod_hours if summary else 0.0,
        })
        total_worked += effective
        total_target += target

    return {
        "days": days,
        "total_worked": round(total_worked, 2),
        "total_target": total_target,
        "week_compliant": total_worked >= (total_target - 0.01),
    }
