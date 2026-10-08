"""Time calculation service — FOSC schedule, BEOD, phone/offsite rollup."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, NamedTuple, Optional

from sqlalchemy.orm import Session

from app.config import WEEKDAY_HOURS, BEOD_MINIMUM_HOURS
from app.models import (
    TimeEntry, OffsiteEntry, PhoneSupportEntry, DailySummary, EntryType, LeaveType,
)


def get_target_hours(work_date: date) -> float:
    """Get the FOSC target hours for a given weekday (9 Mon–Thu, 4 Fri)."""
    return float(WEEKDAY_HOURS.get(work_date.weekday(), 0))


BEOD_CREDIT_HOURS = 1.0
# Allowed BEOD credits. 1h is the usual paid end-of-day break.
# Shorter lengths cover a split day (e.g. 30m lunch + 30m BEOD).
BEOD_LENGTH_HOURS = (1.0, 0.75, 0.5, 0.25)


class ProjectedCheckout(NamedTuple):
    """Standard (9h / 4h) and optional earlier BEOD (8h Mon–Thu) checkout times."""
    standard: Optional[datetime]
    beod: Optional[datetime]
    beod_claimed: bool = False


def hours_to_meet_target(work_date: date, *, beod: bool = False) -> float:
    """Clock hours needed to meet FOSC target.

    Mon–Thu: 9h, or 8h when taking BEOD (+1h lunch credit).
    Friday: 4h. BEOD is not used — 4h is below the 6h BEOD floor.
    """
    target = get_target_hours(work_date)
    if target <= 0:
        return 0.0
    if beod:
        needed = target - BEOD_CREDIT_HOURS
        if needed >= BEOD_MINIMUM_HOURS:
            return needed
    return target


def beod_offered_on(work_date: date) -> bool:
    """BEOD checkbox is only offered when it can shorten the required clock day.

    True Mon–Thu (9h → 8h). False Friday (4h, below the 6h BEOD floor) and weekends.
    """
    return hours_to_meet_target(work_date, beod=True) < hours_to_meet_target(work_date)


def projected_checkout_time(
    checkin_time: datetime,
    work_date: date,
    completed_hours: float = 0.0,
    *,
    beod: bool = False,
) -> Optional[datetime]:
    """Projected checkout = open check-in + remaining hours to hit the day target.

    Returns None when there is no remaining target (weekend, already met, or
    a return punch after a full day). Callers should hide the projection after
    checkout (no open check-in) and after midnight (new work_date).
    """
    remaining = hours_to_meet_target(work_date, beod=beod) - (completed_hours or 0.0)
    if remaining <= 0:
        return None
    return checkin_time + timedelta(hours=remaining)


def projected_checkout_from_entries(
    time_entries: List[TimeEntry],
    work_date: date,
    *,
    beod_claimed: bool = False,
) -> ProjectedCheckout:
    """Projected checkout for an open session (empty if checked out / not in)."""
    pending: Optional[TimeEntry] = None
    for entry in sorted(time_entries, key=lambda e: (e.declared_time, e.id or 0)):
        if entry.entry_type == EntryType.check_in:
            pending = entry
        elif entry.entry_type == EntryType.check_out:
            pending = None
    if pending is None:
        return ProjectedCheckout(None, None, False)

    completed = calculate_clock_hours(time_entries)
    standard = projected_checkout_time(
        pending.declared_time, work_date, completed, beod=False,
    )
    beod = projected_checkout_time(
        pending.declared_time, work_date, completed, beod=True,
    )
    if beod_claimed:
        return ProjectedCheckout(beod or standard, None, True)
    if beod is not None and beod == standard:
        beod = None
    return ProjectedCheckout(standard, beod, False)


def normalize_beod_length(hours) -> float:
    """Snap a requested BEOD credit to an allowed length. Unknown → 1 hour."""
    try:
        value = float(hours)
    except (TypeError, ValueError):
        return BEOD_CREDIT_HOURS
    for choice in BEOD_LENGTH_HOURS:
        if abs(choice - value) < (1.0 / 60.0):
            return choice
    return BEOD_CREDIT_HOURS


def format_beod_length(hours: float) -> str:
    """Compact label: 1h, 45m, 30m, 15m."""
    minutes = int(round(float(hours) * 60))
    labels = {60: "1h", 45: "45m", 30: "30m", 15: "15m"}
    return labels.get(minutes, f"{float(hours):g}h")


def beod_hours_value(hours) -> str:
    """Option value for a BEOD length select: '1', '0.75', '0.5', or '0.25'."""
    normalized = normalize_beod_length(hours)
    if abs(normalized - 0.75) < 1e-6:
        return "0.75"
    if abs(normalized - 0.5) < 1e-6:
        return "0.5"
    if abs(normalized - 0.25) < 1e-6:
        return "0.25"
    return "1"


def beod_pref_ui(employee, offered: bool = True, claim: bool | None = None, hours=None) -> dict:
    """Template defaults for the BEOD checkbox and length.

    Uses the employee's last choice when claim/hours are not overridden.
    Days that do not offer BEOD stay unchecked unless claim is passed in.
    """
    known = bool(getattr(employee, "beod_pref_known", False)) if employee is not None else False
    pref_claim = bool(getattr(employee, "beod_pref_claim", False)) if known else False
    pref_hours = getattr(employee, "beod_pref_hours", None) if employee is not None else None
    use_claim = pref_claim if claim is None else bool(claim)
    if not offered and claim is None:
        use_claim = False
    use_hours = pref_hours if hours is None else hours
    return {
        "beod_pref_known": known,
        "beod_pref_claim": bool(use_claim),
        "beod_pref_hours": beod_hours_value(use_hours or BEOD_CREDIT_HOURS),
    }


def remember_beod_pref(employee, claimed: bool, hours, work_date: date) -> None:
    """Store this person's BEOD choice for the next day.

    Unchecking keeps the last length, so the dropdown is ready if they claim again.
    Friday (and any day BEOD is not offered) does not change the routine.
    """
    if employee is None or not beod_offered_on(work_date):
        return
    employee.beod_pref_known = True
    employee.beod_pref_claim = bool(claimed)
    if claimed:
        employee.beod_pref_hours = normalize_beod_length(hours)
    elif not employee.beod_pref_hours:
        employee.beod_pref_hours = BEOD_CREDIT_HOURS


def resolve_beod_request(claimed: bool, explicit: float | None, stored: float | None) -> float:
    """Hours of BEOD requested. 0 if not claimed.

    An explicit choice wins. Legacy claims with nothing stored stay 1 hour.
    """
    if not claimed:
        return 0.0
    if explicit is not None:
        return normalize_beod_length(explicit)
    if stored and stored > 0:
        return normalize_beod_length(stored)
    return BEOD_CREDIT_HOURS


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
        if not offsite.end_time or not offsite.start_time:
            continue
        delta = offsite.end_time - offsite.start_time
        if delta.total_seconds() > 0:
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
    beod_requested_hours: float | None = None,
) -> DailySummary:
    """Recalculate and update/create the daily summary for an employee.

    FOSC Normal Time = clock + offsite + phone + BEOD credit (claimed length,
    default 1h, when approved and work hours before credit >= BEOD_MINIMUM_HOURS).

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

    eff_requested = resolve_beod_request(
        eff_beod_claimed,
        beod_requested_hours,
        summary.beod_requested_hours if summary else None,
    )

    beod_hours = 0.0
    total_hours = work_hours
    if eff_beod_claimed and eff_beod_approved and work_hours >= BEOD_MINIMUM_HOURS:
        credit = eff_requested if eff_requested > 0 else BEOD_CREDIT_HOURS
        beod_hours = credit
        total_hours = round(work_hours + credit, 2)
    elif eff_beod_claimed and work_hours < BEOD_MINIMUM_HOURS:
        # Claimed but below floor — no credit; keep claim + requested length
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
        summary.beod_requested_hours = eff_requested
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
            beod_requested_hours=eff_requested,
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
