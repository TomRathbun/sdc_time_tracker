"""End-of-day reminder for a missed punch, and the reply that fixes the simple cases."""

from __future__ import annotations

import logging
import re
import secrets
from datetime import date, datetime, time, timedelta
from html import escape

from sqlalchemy.orm import Session

from app.config import APP_NAME, EMAIL_ENABLED
from app.models import (
    DailySummary, Employee, EntryType, InboundEmail, LeaveRequest, LeaveStatus,
    LeaveType, LocationType, MissedEntryToken, OffsiteEntry, PhoneSupportEntry,
    Role, TimeEntry,
)
from app.services.audit import log_action
from app.services.email import _send_email
from app.services.email_layout import render_email
from app.services.leave_sync import apply_leave_approval
from app.services.settings import get_bool_setting, get_setting, parse_hhmm_setting
from app.services.time_calc import friday_required_hours, get_target_hours, update_daily_summary
from app.services.time_state import STATUS_CHECKED_IN, STATUS_NOT_STARTED, can_check_out, current_status

logger = logging.getLogger(__name__)

_WINDOW_MINUTES = 180
_CODE_RE = re.compile(r"(?im)^\s*CODE:\s*([A-Fa-f0-9]{8})\s*$")
_DAY_RE = re.compile(r"(?im)^\s*DAY:\s*(.+?)\s*$")
_OUT_RE = re.compile(r"(?im)^\s*CHECK\s*OUT:\s*(\d{1,2}):(\d{2})\s*$")
_REASON_RE = re.compile(r"(?im)^\s*REASON:\s*(.+?)\s*$")

_LEAVE_WORDS = {
    "vacation": LeaveType.vacation,
    "vac": LeaveType.vacation,
    "pto": LeaveType.vacation,
    "sick": LeaveType.sick,
    "covid": LeaveType.covid_sick,
    "covid sick": LeaveType.covid_sick,
    "national holiday": LeaveType.uae_holiday,
    "holiday": LeaveType.uae_holiday,
    "uae holiday": LeaveType.uae_holiday,
}
_LEAVE_LABEL = {
    LeaveType.vacation: "Vacation",
    LeaveType.sick: "Sick",
    LeaveType.covid_sick: "COVID",
    LeaveType.uae_holiday: "National Holiday",
}


def _normalize_reply(body: str) -> str:
    lines = []
    for line in (body or "").splitlines():
        stripped = line.lstrip()
        if stripped.startswith(">"):
            stripped = stripped[1:].lstrip()
        lines.append(stripped)
    return "\n".join(lines)


def _notify(employee: Employee, title: str, text: str, reply_message_id: str = "", reply_subject: str = "") -> None:
    address = (employee.email or "").strip()
    if not address:
        return
    message_id = (reply_message_id or "").strip()
    if message_id and not message_id.startswith("<"):
        message_id = f"<{message_id.strip('<>')}>"
    subject = title
    if message_id:
        base = (reply_subject or title).strip()
        subject = base if base.lower().startswith("re:") else f"Re: {base}"
    html = render_email(
        title,
        "".join(
            f'<p style="margin:0 0 10px;">{escape(line) if line else "&nbsp;"}</p>'
            for line in text.splitlines()
        ),
        kicker=APP_NAME,
    )
    if not _send_email(address, subject, html, text, in_reply_to=message_id):
        logger.error("Missed-entry confirmation was not sent to %s", address)


def _issue(db: Session, employee: Employee, work_date: date, kind: str, checkin_id: int | None) -> MissedEntryToken | None:
    existing = (
        db.query(MissedEntryToken)
        .filter(
            MissedEntryToken.employee_id == employee.id,
            MissedEntryToken.work_date == work_date,
        )
        .first()
    )
    if existing:
        return None
    token = MissedEntryToken(
        employee_id=employee.id,
        work_date=work_date,
        kind=kind,
        code=secrets.token_hex(4).upper(),
        checkin_entry_id=checkin_id,
        expires_at=datetime.combine(work_date + timedelta(days=1), time(12, 0)),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token


def _send_absent(employee: Employee, work_date: date, code: str) -> bool:
    when = work_date.strftime("%A %d %b")
    fields = f"CODE: {code}\nDAY: Vacation"
    text = (
        f"Hello {employee.name},\n\n"
        f"We don't have a check-in or a check-out for you on {when}. "
        "If you were out, reply to this email. Leave the code unchanged and set DAY to one of:\n"
        "Vacation, Sick, COVID, or National Holiday.\n\n"
        "The confirmation can take up to 5 minutes.\n"
        "If the day was more complicated than a full day out, sign in on the tracker and fix it there.\n\n"
        f"{fields}\n"
    )
    html = render_email(
        "Did you miss today?",
        f"""
        <p style="margin:8px 0 12px;">We don't have a check-in or a check-out for you on <strong style="color:#f8fafc;">{escape(when)}</strong>.</p>
        <p style="margin:0 0 12px;">If you were out, reply to this email. Leave the code unchanged and set DAY to one of: Vacation, Sick, COVID, or National Holiday.</p>
        <p style="margin:0 0 12px;">The confirmation can take up to 5 minutes. If the day was more complicated than a full day out, sign in on the tracker and fix it there.</p>
        <pre style="margin:0;background:#020617;color:#f8fafc;padding:16px;border-radius:12px;border:1px solid rgba(255,255,255,0.08);font-family:'JetBrains Mono',Consolas,monospace;">{escape(fields)}</pre>
        """,
        kicker="Missed time entry",
    )
    return _send_email(
        employee.email.strip(),
        f"Missed time entry — {when}",
        html,
        text,
    )


def _send_checkout(employee: Employee, checkin: datetime, code: str) -> bool:
    fields = f"CODE: {code}\nCHECK OUT: {checkin.strftime('%H:%M')}\nREASON: "
    text = (
        f"Hello {employee.name},\n\n"
        f"You checked in at {checkin.strftime('%H:%M')} and there is no check-out yet. "
        "Reply to this email. Leave the code unchanged, set CHECK OUT to the time you left, and add a short reason.\n\n"
        "The confirmation can take up to 5 minutes.\n"
        "If the day needs more than a checkout time, sign in on the tracker and fix it there.\n\n"
        f"{fields}\n"
    )
    html = render_email(
        "Still checked in",
        f"""
        <p style="margin:8px 0 12px;">You checked in at <strong style="color:#f8fafc;">{checkin.strftime('%H:%M')}</strong> and there is no check-out yet.</p>
        <p style="margin:0 0 12px;">Reply to this email. Leave the code unchanged, set CHECK OUT to the time you left, and add a short reason.</p>
        <p style="margin:0 0 12px;">The confirmation can take up to 5 minutes. If the day needs more than a checkout time, sign in on the tracker and fix it there.</p>
        <pre style="margin:0;background:#020617;color:#f8fafc;padding:16px;border-radius:12px;border:1px solid rgba(255,255,255,0.08);font-family:'JetBrains Mono',Consolas,monospace;">{escape(fields)}</pre>
        """,
        kicker="Missed check-out",
    )
    return _send_email(
        employee.email.strip(),
        f"Missed check-out — {checkin.strftime('%H:%M')}",
        html,
        text,
    )


def remind_missed_entries(db: Session, now: datetime | None = None) -> int:
    """Email the simple misses once, inside the configured end-of-day window."""
    if not EMAIL_ENABLED or not get_bool_setting(db, "missed_entry_enabled"):
        return 0
    now = now or datetime.now()
    today = now.date()
    if get_target_hours(today) <= 0:
        return 0
    start = parse_hhmm_setting(get_setting(db, "missed_entry_time"), "1730")
    now_min = now.hour * 60 + now.minute
    if not (start <= now_min <= start + _WINDOW_MINUTES):
        return 0

    sent = 0
    people = (
        db.query(Employee)
        .filter(
            Employee.is_active == True,
            Employee.role != Role.customer,
            Employee.email != None,
            Employee.email != "",
        )
        .all()
    )
    for person in people:
        if (
            db.query(MissedEntryToken)
            .filter(MissedEntryToken.employee_id == person.id, MissedEntryToken.work_date == today)
            .first()
        ):
            continue
        summary = (
            db.query(DailySummary)
            .filter(DailySummary.employee_id == person.id, DailySummary.date == today)
            .first()
        )
        if summary and summary.leave_approved and summary.leave_hours:
            continue
        status = current_status(db, person.id, today)
        if status == STATUS_NOT_STARTED:
            if friday_required_hours(db, person.id, today) <= 0:
                continue
            has_other = (
                db.query(OffsiteEntry).filter(
                    OffsiteEntry.employee_id == person.id, OffsiteEntry.date == today,
                ).first()
                or db.query(PhoneSupportEntry).filter(
                    PhoneSupportEntry.employee_id == person.id, PhoneSupportEntry.date == today,
                ).first()
            )
            if has_other:
                continue
            token = _issue(db, person, today, "absent", None)
            if token and _send_absent(person, today, token.code):
                sent += 1
            elif token:
                db.delete(token)
                db.commit()
        elif status == STATUS_CHECKED_IN:
            checkin = (
                db.query(TimeEntry)
                .filter(
                    TimeEntry.employee_id == person.id,
                    TimeEntry.date == today,
                    TimeEntry.entry_type == EntryType.check_in,
                )
                .order_by(TimeEntry.declared_time.desc())
                .first()
            )
            if checkin is None:
                continue
            token = _issue(db, person, today, "checkout", checkin.id)
            if token and _send_checkout(person, checkin.declared_time, token.code):
                sent += 1
            elif token:
                db.delete(token)
                db.commit()
    return sent


def _apply_leave(db: Session, token: MissedEntryToken, employee: Employee, leave_type: LeaveType) -> str | None:
    if current_status(db, employee.id, token.work_date) != STATUS_NOT_STARTED:
        return "This day already has a time entry. Sign in on the tracker to change it."
    target = get_target_hours(token.work_date)
    if target <= 0:
        return "That date is not a workday."
    leave = LeaveRequest(
        employee_id=employee.id,
        leave_type=leave_type,
        start_date=token.work_date,
        end_date=token.work_date,
        status=LeaveStatus.approved,
        comments="Recorded from the missed-day email.",
        created_at=datetime.utcnow(),
    )
    db.add(leave)
    db.commit()
    db.refresh(leave)
    apply_leave_approval(db, leave)
    log_action(
        db, action="email_missed_leave", entity_type="LeaveRequest",
        entity_id=leave.id, employee_id=employee.id,
        new_values={"date": token.work_date.isoformat(), "type": leave_type.value, "hours": target},
    )
    return None


def _apply_checkout(db: Session, token: MissedEntryToken, employee: Employee, declared: datetime, reason: str) -> str | None:
    if declared > datetime.now():
        return "That check-out time has not happened yet."
    state_err = can_check_out(db, employee.id, token.work_date, declared_time=declared)
    if state_err:
        return state_err + " Sign in on the tracker if this day needs more than a check-out."
    entry = TimeEntry(
        employee_id=employee.id,
        date=token.work_date,
        declared_time=declared,
        submission_time=datetime.utcnow(),
        entry_type=EntryType.check_out,
        location_type=LocationType.office,
        is_remote=False,
        comments=reason,
        offset_approved=True,
    )
    db.add(entry)
    db.commit()
    update_daily_summary(db, employee.id, token.work_date)
    log_action(
        db, action="email_missed_checkout", entity_type="TimeEntry",
        entity_id=entry.id, employee_id=employee.id,
        new_values={"time": declared.strftime("%H:%M"), "reason": reason},
    )
    return None


def process_missed_replies(db: Session) -> int:
    """Apply replies to the missed-entry mail. Returns how many were recorded."""
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
        body = _normalize_reply(message.body_text or "")
        code_match = _CODE_RE.search(body) or re.search(r"CODE:\s*([A-Fa-f0-9]{8})", body, re.I)
        if not code_match:
            continue
        code = code_match.group(1).upper()
        token = db.query(MissedEntryToken).filter(MissedEntryToken.code == code).first()
        if token is None or token.notified_uid == message.imap_uid:
            continue
        employee = db.query(Employee).filter(Employee.id == token.employee_id).first()
        if employee is None:
            continue
        sender = (message.from_addr or "").strip().lower()
        owner = (employee.email or "").strip().lower()
        if not owner or sender != owner:
            token.notified_uid = message.imap_uid
            db.commit()
            continue

        fail = None
        if token.used_at is not None:
            fail = f"Code {token.code} was already used."
        elif datetime.now() > token.expires_at:
            fail = f"Code {token.code} has expired. Sign in on the tracker to fix this day."
        elif token.kind == "absent":
            day_match = _DAY_RE.search(body)
            chosen = (day_match.group(1).strip().lower() if day_match else "")
            leave_type = _LEAVE_WORDS.get(chosen)
            if leave_type is None:
                fail = "Set DAY to Vacation, Sick, COVID, or National Holiday."
            else:
                fail = _apply_leave(db, token, employee, leave_type)
                if fail is None:
                    label = _LEAVE_LABEL[leave_type]
                    hours = get_target_hours(token.work_date)
                    _notify(
                        employee,
                        f"{label} recorded",
                        f"Accepted. {token.work_date.strftime('%A %d %b')} is {label} ({hours:g} hours).",
                        reply_message_id=message.message_id or "",
                        reply_subject=message.subject or "",
                    )
                    token.used_at = datetime.utcnow()
                    token.notified_uid = message.imap_uid
                    db.commit()
                    done += 1
                    continue
        else:
            clock = _OUT_RE.search(body)
            reason_match = _REASON_RE.search(body)
            reason = (reason_match.group(1).strip() if reason_match else "")
            if not clock:
                fail = "Set CHECK OUT to a time like 16:30, and add a REASON."
            elif len(reason) < 3:
                fail = "Add a short REASON. The code stays the same."
            else:
                hour, minute = int(clock.group(1)), int(clock.group(2))
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    fail = "CHECK OUT must look like 16:30."
                else:
                    declared = datetime.combine(token.work_date, time(hour, minute))
                    fail = _apply_checkout(db, token, employee, declared, reason)
                    if fail is None:
                        _notify(
                            employee,
                            f"Checked out at {declared.strftime('%H:%M')}",
                            f"Accepted. You are checked out at {declared.strftime('%H:%M')}.\nReason: {reason}",
                            reply_message_id=message.message_id or "",
                            reply_subject=message.subject or "",
                        )
                        token.used_at = datetime.utcnow()
                        token.notified_uid = message.imap_uid
                        db.commit()
                        done += 1
                        continue
        if fail:
            _notify(
                employee,
                "That reply was not recorded",
                fail + "\n\nYou can reply again with the same code, or sign in on the tracker.",
                reply_message_id=message.message_id or "",
                reply_subject=message.subject or "",
            )
            token.notified_uid = message.imap_uid
            db.commit()
    return done
