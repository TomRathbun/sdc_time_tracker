"""One-time check-in codes mailed before the usual start.

The employee replies with the code and a declared time. The declared time
may be later than the reply. It cannot be more than 15 minutes earlier.
The code expires at noon.
"""

from __future__ import annotations

import logging
import secrets
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models import (
    CheckinToken, Employee, EntryType, InboundEmail, LeaveRequest, LeaveStatus,
    LocationType, TimeEntry,
)
from app.services.audit import log_action
from app.services.email_checkout import _notify, parse_checkout_reply
from app.services.time_calc import get_target_hours, update_daily_summary
from app.services.time_state import can_check_in

logger = logging.getLogger(__name__)


def usual_checkin_time(db: Session, employee_id: int, today: date) -> datetime | None:
    """Median first check-in from recent weekdays. Same weekday when we have two."""
    start = today - timedelta(days=28)
    rows = (
        db.query(TimeEntry)
        .filter(
            TimeEntry.employee_id == employee_id,
            TimeEntry.entry_type == EntryType.check_in,
            TimeEntry.date >= start,
            TimeEntry.date < today,
        )
        .all()
    )
    first: dict[date, datetime] = {}
    for row in rows:
        if row.date.weekday() >= 5:
            continue
        current = first.get(row.date)
        if current is None or row.declared_time < current:
            first[row.date] = row.declared_time
    same = [stamp for day, stamp in first.items() if day.weekday() == today.weekday()]
    pool = same if len(same) >= 2 else list(first.values())
    if not pool:
        return None
    minutes = sorted(stamp.hour * 60 + stamp.minute for stamp in pool)
    mid = minutes[len(minutes) // 2]
    return datetime.combine(today, time(mid // 60, mid % 60))


def _on_leave(db: Session, employee_id: int, today: date) -> bool:
    target = get_target_hours(today)
    from app.models import DailySummary
    summary = (
        db.query(DailySummary)
        .filter(DailySummary.employee_id == employee_id, DailySummary.date == today)
        .first()
    )
    if summary and summary.leave_hours and target and float(summary.leave_hours) >= target - 0.05:
        return True
    request = (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status.in_([LeaveStatus.pending, LeaveStatus.approved]),
            LeaveRequest.start_date <= today,
            LeaveRequest.end_date >= today,
        )
        .first()
    )
    return request is not None


def issue_checkin_token(db: Session, employee: Employee, expected: datetime) -> CheckinToken | None:
    """Create today's code. None if one was already mailed."""
    existing = (
        db.query(CheckinToken)
        .filter(
            CheckinToken.employee_id == employee.id,
            CheckinToken.work_date == expected.date(),
        )
        .first()
    )
    if existing:
        return None
    token = CheckinToken(
        employee_id=employee.id,
        work_date=expected.date(),
        code=secrets.token_hex(4).upper(),
        expected_time=expected,
        expires_at=datetime.combine(expected.date(), time(12, 0)),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token


def cancel_checkin_token(db: Session, token: CheckinToken) -> None:
    db.delete(token)
    db.commit()


def _apply(db: Session, token: CheckinToken, employee: Employee, parsed: dict, now: datetime, reply_at: datetime):
    declared = datetime.combine(token.work_date, time(parsed["hour"], parsed["minute"]))
    state_err = can_check_in(db, employee.id, token.work_date)
    if state_err:
        return f"Rejected because {state_err}"
    early = (reply_at - declared).total_seconds() / 60
    if early > 15:
        earliest = (reply_at - timedelta(minutes=15)).strftime("%H:%M")
        return (
            f"Rejected because {declared.strftime('%H:%M')} is more than 15 minutes earlier than "
            f"when you sent this reply ({reply_at.strftime('%H:%M')}). "
            f"A reply sent at {reply_at.strftime('%H:%M')} cannot use a time before {earliest}. "
            "A later time is allowed. Reply again with the same CODE."
        )
    entry = TimeEntry(
        employee_id=employee.id,
        date=token.work_date,
        declared_time=declared,
        submission_time=now,
        entry_type=EntryType.check_in,
        location_type=LocationType.office,
        is_remote=False,
        comments="Email check-in",
        offset_approved=True,
    )
    db.add(entry)
    db.commit()
    update_daily_summary(db, employee.id, token.work_date)
    token.used_at = now
    db.commit()
    log_action(
        db, action="email_checkin", entity_type="TimeEntry",
        entity_id=entry.id, employee_id=employee.id,
        new_values={
            "declared_time": str(declared),
            "submission_time": str(now),
            "code": token.code,
        },
    )
    return declared


def process_checkin_replies(db: Session) -> int:
    """Apply new inbox replies that carry a check-in code. Returns how many checked in."""
    since = datetime.utcnow() - timedelta(days=2)
    messages = (
        db.query(InboundEmail)
        .filter(InboundEmail.fetched_at >= since)
        .order_by(InboundEmail.id.desc())
        .limit(40)
        .all()
    )
    done = 0
    for message in messages:
        parsed = parse_checkout_reply(message.body_text or "")
        if not parsed:
            continue
        token = db.query(CheckinToken).filter(CheckinToken.code == parsed["code"]).first()
        if token is None or token.notified_uid == message.imap_uid:
            continue
        employee = db.query(Employee).filter(Employee.id == token.employee_id).first()
        if employee is None:
            continue
        sender = (message.from_addr or "").strip().lower()
        owner = (employee.email or "").strip().lower()
        if not owner or sender != owner:
            logger.warning("Check-in code %s ignored from %s", token.code, sender or "(blank)")
            token.notified_uid = message.imap_uid
            db.commit()
            continue

        fail = None
        if parsed.get("bad_time"):
            fail = "Rejected because DECLARED TIME must look like 06:30."
        elif token.used_at is not None:
            fail = f"Rejected because code {token.code} was already used."
        elif datetime.now() > token.expires_at:
            fail = f"Rejected because code {token.code} expired at noon. Check in on the kiosk."
        else:
            try:
                reply_at = message.received_at or datetime.now()
                result = _apply(db, token, employee, parsed, datetime.now(), reply_at)
            except Exception as exc:
                db.rollback()
                logger.exception("Email check-in failed")
                fail = f"Rejected because the check-in could not be recorded. {exc}"
                result = None
            if isinstance(result, str):
                fail = result
            elif result is not None:
                when = result.strftime("%H:%M")
                _notify(
                    employee,
                    f"Checked in at {when}",
                    f"Accepted. You are checked in at {when}.",
                    reply_message_id=message.message_id or "",
                    reply_subject=message.subject or "",
                )
                token.notified_uid = message.imap_uid
                db.commit()
                done += 1
                continue
        if fail:
            _notify(
                employee,
                "Check-in reply was not recorded",
                fail + "\n\nThe CODE stays the same if you reply again before noon.",
                reply_message_id=message.message_id or "",
                reply_subject=message.subject or "",
            )
            token.notified_uid = message.imap_uid
            db.commit()
    return done
