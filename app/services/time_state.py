"""Punch / offsite state helpers — single source of truth for day status."""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.models import EntryType, OffsiteEntry, TimeEntry

# Day punch status values
STATUS_NOT_STARTED = "not_started"
STATUS_CHECKED_IN = "checked_in"
STATUS_CHECKED_OUT = "checked_out"


def latest_entry(
    db: Session,
    employee_id: int,
    work_date: date,
) -> Optional[TimeEntry]:
    """Return the latest time entry for employee/day by declared_time."""
    return (
        db.query(TimeEntry)
        .filter(
            TimeEntry.employee_id == employee_id,
            TimeEntry.date == work_date,
        )
        .order_by(TimeEntry.declared_time.desc(), TimeEntry.id.desc())
        .first()
    )


def current_status(
    db: Session,
    employee_id: int,
    work_date: date,
) -> str:
    """Status from the latest entry: not_started | checked_in | checked_out."""
    last = latest_entry(db, employee_id, work_date)
    if not last:
        return STATUS_NOT_STARTED
    if last.entry_type == EntryType.check_in:
        return STATUS_CHECKED_IN
    return STATUS_CHECKED_OUT


def can_check_in(db: Session, employee_id: int, work_date: date) -> Optional[str]:
    """Return an error message if check-in is not allowed, else None."""
    status = current_status(db, employee_id, work_date)
    if status == STATUS_CHECKED_IN:
        return "You are already checked in. Check out before checking in again."
    return None


def can_check_out(
    db: Session,
    employee_id: int,
    work_date: date,
    declared_time: Optional[datetime] = None,
) -> Optional[str]:
    """Return an error message if check-out is not allowed, else None."""
    status = current_status(db, employee_id, work_date)
    if status == STATUS_NOT_STARTED:
        return "No open check-in found. Check in first."
    if status == STATUS_CHECKED_OUT:
        return "You are already checked out. Check in before checking out again."

    if declared_time is not None:
        open_checkin = latest_entry(db, employee_id, work_date)
        if open_checkin and declared_time < open_checkin.declared_time:
            return (
                f"Check-out time cannot be earlier than check-in "
                f"({open_checkin.declared_time.strftime('%H:%M')})."
            )
    return None


def ranges_overlap(
    a_start: datetime,
    a_end: datetime,
    b_start: datetime,
    b_end: datetime,
) -> bool:
    """True if half-open intervals [start, end) overlap."""
    return a_start < b_end and b_start < a_end


def paired_clock_intervals(time_entries: Sequence[TimeEntry]) -> List[Tuple[datetime, datetime]]:
    """Pair check-in/check-out entries into closed work intervals."""
    sorted_entries = sorted(time_entries, key=lambda e: (e.declared_time, e.id or 0))
    intervals: List[Tuple[datetime, datetime]] = []
    pending: Optional[TimeEntry] = None
    for entry in sorted_entries:
        if entry.entry_type == EntryType.check_in:
            pending = entry
        elif entry.entry_type == EntryType.check_out and pending:
            intervals.append((pending.declared_time, entry.declared_time))
            pending = None
    return intervals


def open_checkin_time(time_entries: Sequence[TimeEntry]) -> Optional[datetime]:
    """If the day ends with an unpaired check-in, return its declared_time."""
    sorted_entries = sorted(time_entries, key=lambda e: (e.declared_time, e.id or 0))
    pending: Optional[datetime] = None
    for entry in sorted_entries:
        if entry.entry_type == EntryType.check_in:
            pending = entry.declared_time
        elif entry.entry_type == EntryType.check_out:
            pending = None
    return pending


def validate_offsite_range(
    db: Session,
    employee_id: int,
    work_date: date,
    start_time: datetime,
    end_time: datetime,
    *,
    exclude_offsite_id: Optional[int] = None,
    extra_clock_intervals: Optional[Sequence[Tuple[datetime, datetime]]] = None,
    ignore_existing_clock: bool = False,
    ignore_existing_offsite: bool = False,
) -> Optional[str]:
    """
    Validate an offsite block (Option A rules).

    - end must be after start
    - must not overlap other offsite entries that day
    - must not overlap completed clock sessions
    - must not fall while still checked in (open session covering the range)

    Returns error message or None if valid.
    """
    if end_time <= start_time:
        return "End time must be after start time."

    if not ignore_existing_offsite:
        offsites = (
            db.query(OffsiteEntry)
            .filter(
                OffsiteEntry.employee_id == employee_id,
                OffsiteEntry.date == work_date,
            )
            .all()
        )
        for oe in offsites:
            if exclude_offsite_id is not None and oe.id == exclude_offsite_id:
                continue
            if ranges_overlap(start_time, end_time, oe.start_time, oe.end_time):
                return (
                    f"Offsite overlaps an existing remote site entry "
                    f"({oe.start_time.strftime('%H:%M')}–{oe.end_time.strftime('%H:%M')})."
                )

    clock_intervals: List[Tuple[datetime, datetime]] = []
    open_start: Optional[datetime] = None

    if not ignore_existing_clock:
        time_entries = (
            db.query(TimeEntry)
            .filter(
                TimeEntry.employee_id == employee_id,
                TimeEntry.date == work_date,
            )
            .all()
        )
        clock_intervals.extend(paired_clock_intervals(time_entries))
        open_start = open_checkin_time(time_entries)

    if extra_clock_intervals:
        clock_intervals.extend(extra_clock_intervals)

    for c_start, c_end in clock_intervals:
        if ranges_overlap(start_time, end_time, c_start, c_end):
            return (
                "Offsite overlaps a checked-in work interval "
                f"({c_start.strftime('%H:%M')}–{c_end.strftime('%H:%M')}). "
                "Log offsite only for time between check-out and the next check-in."
            )

    # Open check-in with no checkout: treat as [open_start, +inf).
    # Overlaps if the offsite ends after the open check-in started.
    if open_start is not None and end_time > open_start:
        return (
            "You are currently checked in (or this offsite overlaps an open session). "
            "Check out before logging remote site work, or use the gap prompt after returning."
        )

    return None
