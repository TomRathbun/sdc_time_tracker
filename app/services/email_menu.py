"""Employee starts by email: hello, a menu, then a form they fill in."""

from __future__ import annotations

import logging
import re
import secrets
from datetime import date, datetime, time, timedelta
from html import escape

from sqlalchemy.orm import Session

from app.config import PAST_DAY_MAX_LOOKBACK_DAYS, SMTP_USER
from app.models import (
    CheckinToken, CheckoutToken, EmailForm, Employee, InboundEmail, LeaveRequest,
    LeaveStatus, LeaveType, MissedEntryToken, OffsiteEntry, PhoneSupportEntry, Role,
    TimeEntry,
)
from app.services.audit import log_action
from app.services.email import _send_email
from app.services.email_checkout import _fresh_reply, _mark_answered, _notify, _split_clock
from app.services.email_layout import render_email
from app.services.leave_balance import can_request_leave, count_workdays, partial_pto_error
from app.services.time_calc import get_target_hours, update_daily_summary
from app.services.time_offset import offset_approved_default
from app.services.time_state import OFFSITE_CHECKOUT_COMMENT, depart_for_offsite, validate_offsite_range

logger = logging.getLogger(__name__)

_FIELD_RE = re.compile(
    r"(?im)^\s*(CODE|DATE|HOURS|NOTE|START|END|PLACE|TYPE)\s*:\s*(.*?)\s*$"
)
_CODE_RE = re.compile(r"(?im)CODE:\s*([A-Fa-f0-9]{8})")
_AUTO_RE = re.compile(r"^(automatic reply|auto:|out of office|undeliverable|mail delivery)", re.I)
_GREETING = {
    "", "hello", "hi", "hey", "menu", "help", "start", "options", "hiya", "good morning",
}
_PHONE_WORDS = {"phone", "phone call", "call", "1"}
_OFFSITE_WORDS = {"offsite", "off site", "remote", "remote site", "2"}
_PTO_WORDS = {"pto", "leave", "vacation", "sick", "3"}
_TIMELINE_WORDS = {"timeline", "status", "4"}
_EOD_WORDS = {"eod", "daily", "end of day", "5"}
_WEEKLY_WORDS = {"weekly", "week", "6"}
_REPORTS = {"timeline", "eod", "weekly", "weekly_last"}


def _employee_for(db: Session, address: str) -> Employee | None:
    target = (address or "").strip().lower()
    if not target:
        return None
    people = db.query(Employee).filter(Employee.email != None, Employee.email != "").all()  # noqa: E711
    found = None
    for person in people:
        if (person.email or "").strip().lower() == target:
            if person.is_active:
                return person
            found = person
    return found


def _clean_choice(fresh: str, subject: str) -> str:
    lines = []
    for line in (fresh or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        if re.match(r"^(sent from my |get outlook for )", raw, re.I):
            continue
        lines.append(raw)
    text = lines[0] if lines else ""
    if not text:
        text = re.sub(r"^(re|fw|fwd)\s*:\s*", "", (subject or ""), flags=re.I).strip()
        if text.lower() in ("(no subject)", "your time tracker menu", "phone call form"):
            text = ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", "", text.lower())).strip()


def _choice(fresh: str, subject: str) -> str:
    key = _clean_choice(fresh, subject)
    if key in ("weekly last", "last week", "week last"):
        return "weekly_last"
    if key in _WEEKLY_WORDS or key.startswith("weekly"):
        return "weekly"
    if key in _EOD_WORDS or key.startswith("eod"):
        return "eod"
    if key in _TIMELINE_WORDS or key.startswith("timeline"):
        return "timeline"
    if key in _PTO_WORDS or key.startswith("pto") or key.startswith("leave"):
        return "pto"
    if key in _OFFSITE_WORDS or key.startswith("offsite") or key.startswith("off site"):
        return "offsite"
    if key in _PHONE_WORDS or key.startswith("phone"):
        return "phone"
    return "menu"


def _recognized(fresh: str, subject: str) -> bool:
    return _choice(fresh, subject) != "menu" or _clean_choice(fresh, subject) in _GREETING


def _fields(body: str) -> dict:
    fresh = _fresh_reply(body or "")
    found = {}
    for source in (fresh, body or ""):
        for match in _FIELD_RE.finditer(source):
            key = match.group(1).upper()
            if key not in found:
                found[key] = match.group(2).strip()
    return found


def _parse_date(raw: str, today: date) -> date | None:
    text = (raw or "").strip()
    if not text or text.lower() == "today":
        return today
    if text.lower() == "yesterday":
        return today - timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%d %b", "%d %B"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt:
            parsed = parsed.replace(year=today.year)
            if parsed.date() > today:
                parsed = parsed.replace(year=today.year - 1)
        return parsed.date()
    return None


def _parse_hours(raw: str) -> float | None:
    text = (raw or "").strip().lower().replace(",", ".")
    if not text:
        return None
    matched = re.fullmatch(r"(\d+(?:\.\d+)?)\s*h(?:ours?)?(?:\s*(\d+)\s*m(?:in)?)?", text)
    if matched:
        hours = float(matched.group(1))
        if matched.group(2):
            hours += int(matched.group(2)) / 60.0
        return round(hours, 2)
    matched = re.fullmatch(r"(\d+)\s*m(?:in(?:utes?)?)?", text)
    if matched:
        return round(int(matched.group(1)) / 60.0, 2)
    matched = re.fullmatch(r"(\d+):(\d{2})", text)
    if matched and int(matched.group(2)) < 60:
        return round(int(matched.group(1)) + int(matched.group(2)) / 60.0, 2)
    matched = re.fullmatch(r"\d+(?:\.\d+)?", text)
    if matched:
        return round(float(text), 2)
    return None


def _send_card(employee: Employee, title: str, kicker: str, paragraphs: list[str], pre: str, message) -> bool:
    body = []
    for line in paragraphs:
        body.append(f'<p style="margin:0 0 12px;color:#cbd5e1;">{escape(line)}</p>')
    if pre:
        body.append(
            '<pre style="margin:0;text-align:left;background:#020617;color:#f8fafc;padding:16px;'
            'border-radius:12px;border:1px solid rgba(255,255,255,0.08);font-size:14px;'
            'font-family:\'JetBrains Mono\',Consolas,monospace;">'
            f"{escape(pre)}</pre>"
        )
    html = render_email(title, "".join(body), kicker=kicker)
    text = "\n\n".join(paragraphs)
    if pre:
        text += "\n\n" + pre
    message_id = (message.message_id or "").strip()
    if message_id and not message_id.startswith("<"):
        message_id = f"<{message_id.strip('<>')}>"
    subject = (message.subject or title).strip()
    if subject and not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    if not subject or subject.lower() in ("re:", "re: (no subject)"):
        subject = title
    return _send_email((employee.email or "").strip(), subject, html, text, in_reply_to=message_id)


def _menu_text(employee: Employee, unknown: bool) -> tuple[str, str]:
    lead = "That was not one of the options." if unknown else "Your email is on the tracker."
    lines = [
        f"{lead} Reply with one word.",
        "",
        "PHONE",
        "Log a phone support call.",
        "",
        "OFFSITE",
        "Log time at another site.",
        "",
        "PTO",
        "Request leave. Full day, or part of a day.",
    ]
    if employee.role == Role.manager:
        lines.extend([
            "",
            "TIMELINE",
            "Who is in, out, or off site today.",
            "",
            "EOD",
            "Today's team summary.",
            "",
            "WEEKLY",
            "This week's team summary. WEEKLY LAST sends the previous week.",
        ])
    lines.extend(["", "MENU", "Show this list again."])
    return "What do you want to do?", "\n".join(lines)


def _issue_form(db: Session, employee: Employee, kind: str) -> EmailForm:
    form = EmailForm(
        employee_id=employee.id,
        kind=kind,
        code=secrets.token_hex(4).upper(),
        expires_at=datetime.now() + timedelta(days=2),
    )
    db.add(form)
    db.commit()
    db.refresh(form)
    return form


def _phone_form_text(form: EmailForm, today: date) -> str:
    return (
        f"CODE: {form.code}\n"
        f"DATE: {today.isoformat()}\n"
        "HOURS: 1.5\n"
        "NOTE:"
    )


def _apply_phone(db: Session, employee: Employee, fields: dict):
    """Record the call. Returns (error, entry). Entry is None when error is set."""
    today = date.today()
    work_date = _parse_date(fields.get("DATE", ""), today)
    if work_date is None:
        return "DATE must look like 2026-10-27, 27/10/2026, today, or yesterday.", None
    if work_date > today:
        return "DATE cannot be in the future.", None
    if work_date < today - timedelta(days=PAST_DAY_MAX_LOOKBACK_DAYS):
        return f"DATE can only be in the last {PAST_DAY_MAX_LOOKBACK_DAYS} days.", None
    hours = _parse_hours(fields.get("HOURS", ""))
    if hours is None:
        return "HOURS must look like 1.5, 90m, or 1:30.", None
    if hours <= 0 or hours > 24:
        return "HOURS must be more than 0 and no more than 24.", None
    note = (fields.get("NOTE") or "").strip()
    entry = PhoneSupportEntry(
        employee_id=employee.id,
        date=work_date,
        hours=hours,
        comments=note,
        submission_time=datetime.now(),
    )
    db.add(entry)
    db.commit()
    update_daily_summary(db, employee.id, work_date)
    log_action(
        db, action="email_phone_support", entity_type="PhoneSupportEntry",
        entity_id=entry.id, employee_id=employee.id,
        new_values={"date": work_date.isoformat(), "hours": hours, "comments": note},
    )
    return None, entry


def _handle_form(db: Session, message, employee: Employee, form: EmailForm, fields: dict) -> bool:
    """Send the result. True when the inbound message should stay closed."""
    if form.employee_id != employee.id:
        sent = _notify(
            employee,
            "That form is for someone else",
            "That form was sent to a different person. Email hello to get your own menu.",
            reply_message_id=message.message_id or "",
            reply_subject=message.subject or "",
        )
        if sent:
            _mark_answered(db, message)
        return sent
    if form.used_at is not None:
        sent = _notify(
            employee,
            "That form was already used",
            "That form was already used. Email hello to get the menu again.",
            reply_message_id=message.message_id or "",
            reply_subject=message.subject or "",
        )
        if sent:
            _mark_answered(db, message)
        return sent
    if datetime.now() > form.expires_at:
        sent = _notify(
            employee,
            "That form has expired",
            "That form has expired. Email hello to get a new one.",
            reply_message_id=message.message_id or "",
            reply_subject=message.subject or "",
        )
        if sent:
            _mark_answered(db, message)
        return sent
    if form.kind == "phone":
        return _finish_phone(db, message, employee, form, fields)
    if form.kind == "offsite":
        return _finish_offsite(db, message, employee, form, fields)
    if form.kind == "pto":
        return _finish_pto(db, message, employee, form, fields)
    return False


def _reject(db, message, employee, title: str, error: str) -> bool:
    sent = _notify(
        employee,
        title,
        error + "\n\nReply again with the same CODE and the corrected line.",
        reply_message_id=message.message_id or "",
        reply_subject=message.subject or "",
    )
    if sent:
        _mark_answered(db, message)
    return sent


def _accept(db, message, employee, form: EmailForm, title: str, text: str, undo) -> bool:
    form.used_at = datetime.now()
    db.commit()
    sent = _notify(
        employee, title, text + "\nEmail hello any time for the menu.",
        reply_message_id=message.message_id or "",
        reply_subject=message.subject or "",
    )
    if sent:
        _mark_answered(db, message)
        return True
    form.used_at = None
    undo()
    db.commit()
    return False


def _finish_phone(db, message, employee, form, fields) -> bool:
    error, entry = _apply_phone(db, employee, fields)
    if error:
        return _reject(db, message, employee, "Phone call was not recorded", error)
    hours = _parse_hours(fields.get("HOURS", ""))
    when = _parse_date(fields.get("DATE", ""), date.today())

    def undo():
        if entry is not None:
            db.delete(entry)
        update_daily_summary(db, employee.id, when or date.today())

    return _accept(
        db, message, employee, form,
        "Phone call recorded",
        f"Accepted. {hours:g} hours of phone support on {when.strftime('%d %b %Y')} are recorded.",
        undo,
    )


def _apply_offsite(db, employee, fields):
    today = date.today()
    work_date = _parse_date(fields.get("DATE", "") or "today", today)
    if work_date is None:
        return "DATE must look like 2026-10-27, 27/10/2026, today, or yesterday.", None, []
    if work_date > today:
        return "DATE cannot be in the future.", None, []
    if work_date < today - timedelta(days=PAST_DAY_MAX_LOOKBACK_DAYS):
        return f"DATE can only be in the last {PAST_DAY_MAX_LOOKBACK_DAYS} days.", None, []
    start = _split_clock(fields.get("START", ""))
    if start is None:
        return "START must look like 09:00 or 0900.", None, []
    end_raw = (fields.get("END") or "").strip()
    end = None
    if end_raw and end_raw.lower() not in ("open", "-", "now"):
        end = _split_clock(end_raw)
        if end is None:
            return "END must look like 11:30 or 1130, or be left blank if you are still there.", None, []
    start_time = datetime.combine(work_date, time(start[0], start[1]))
    end_time = datetime.combine(work_date, time(end[0], end[1])) if end else None
    place = (fields.get("PLACE") or "").strip() or "Remote site"
    note = (fields.get("NOTE") or "").strip()
    now = datetime.now()
    before = {
        row.id for row in db.query(TimeEntry).filter(
            TimeEntry.employee_id == employee.id, TimeEntry.date == work_date,
        ).all()
    }
    if work_date == today:
        err = depart_for_offsite(
            db, employee.id, work_date, start_time, now,
            offset_approved=offset_approved_default(db, start_time, now),
        )
        if err:
            db.rollback()
            return err, None, []
    err = validate_offsite_range(db, employee.id, work_date, start_time, end_time)
    if err:
        db.rollback()
        return err, None, []
    entry = OffsiteEntry(
        employee_id=employee.id,
        date=work_date,
        location=place,
        start_time=start_time,
        end_time=end_time,
        comments=note,
        submission_time=now,
        needs_review=True,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    update_daily_summary(db, employee.id, work_date)
    new_ids = [
        row.id for row in db.query(TimeEntry).filter(
            TimeEntry.employee_id == employee.id, TimeEntry.date == work_date,
        ).all()
        if row.id not in before
    ]
    log_action(
        db, action="email_offsite", entity_type="OffsiteEntry",
        entity_id=entry.id, employee_id=employee.id,
        new_values={
            "date": work_date.isoformat(),
            "location": place,
            "start": start_time.strftime("%H:%M"),
            "end": end_time.strftime("%H:%M") if end_time else "",
            "comments": note,
        },
    )
    return None, entry, new_ids


def _finish_offsite(db, message, employee, form, fields) -> bool:
    error, entry, checkout_ids = _apply_offsite(db, employee, fields)
    if error:
        return _reject(db, message, employee, "Off site was not recorded", error)
    when = entry.date
    end = "still there" if entry.end_time is None else entry.end_time.strftime("%H:%M")

    def undo():
        for row in db.query(TimeEntry).filter(TimeEntry.id.in_(checkout_ids)).all():
            if row.comments == OFFSITE_CHECKOUT_COMMENT:
                db.delete(row)
        db.delete(entry)
        update_daily_summary(db, employee.id, when)

    return _accept(
        db, message, employee, form,
        "Off site recorded",
        (
            f"Accepted. Off site at {entry.location} on {when.strftime('%d %b %Y')}, "
            f"{entry.start_time.strftime('%H:%M')} to {end}."
        ),
        undo,
    )


def _leave_type(raw: str) -> LeaveType | None:
    key = re.sub(r"[^a-z]", "", (raw or "").lower())
    return {
        "vacation": LeaveType.vacation,
        "v": LeaveType.vacation,
        "pto": LeaveType.vacation,
        "sick": LeaveType.sick,
        "s": LeaveType.sick,
        "covid": LeaveType.covid_sick,
        "covidsick": LeaveType.covid_sick,
        "holiday": LeaveType.uae_holiday,
        "uaeholiday": LeaveType.uae_holiday,
        "nationalholiday": LeaveType.uae_holiday,
    }.get(key)


def _apply_pto(db, employee, fields):
    today = date.today()
    kind = _leave_type(fields.get("TYPE", ""))
    if kind is None:
        return "TYPE must be vacation, sick, covid, or holiday.", None
    start = _parse_date(fields.get("START") or fields.get("DATE") or "", today)
    end = _parse_date(fields.get("END") or fields.get("START") or fields.get("DATE") or "", today)
    if start is None or end is None:
        return "START and END must look like 2026-10-28 or 28/10/2026.", None
    if end < start:
        return "END must be on or after START.", None
    hours_raw = (fields.get("HOURS") or "").strip().lower()
    full = hours_raw in ("", "full", "day", "all", "fullday", "full day")
    hours = None if full else _parse_hours(hours_raw)
    if not full and hours is None:
        return "HOURS must be full, or a number such as 4.5.", None
    note = (fields.get("NOTE") or "").strip()
    target = get_target_hours(start)
    if hours is not None and (end != start):
        return "Use HOURS: full when the leave covers more than one day.", None
    if hours is not None and hours + 0.001 >= target and target > 0:
        full = True
        hours = None
    if full:
        if start < today - timedelta(days=PAST_DAY_MAX_LOOKBACK_DAYS):
            return f"START can only be in the last {PAST_DAY_MAX_LOOKBACK_DAYS} days, or later.", None
        if end > today + timedelta(days=366):
            return "END is too far ahead.", None
        err = can_request_leave(db, employee, kind, start, end)
        if err:
            return err, None
        leave = LeaveRequest(
            employee_id=employee.id,
            leave_type=kind,
            start_date=start,
            end_date=end,
            status=LeaveStatus.pending,
            comments=note or "Requested by email.",
            created_at=datetime.now(),
        )
        db.add(leave)
        db.commit()
        db.refresh(leave)
        days = count_workdays(start, end)
        log_action(
            db, action="email_pto", entity_type="LeaveRequest",
            entity_id=leave.id, employee_id=employee.id,
            new_values={
                "type": kind.value, "start": start.isoformat(), "end": end.isoformat(),
                "full": True, "workdays": days,
            },
        )
        label = kind.value.replace("_", " ")
        return None, (
            f"Accepted. {label.capitalize()} for {days} workday(s), "
            f"{start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}. "
            "Pending manager approval.",
            lambda: db.delete(leave),
        )
    if target <= 0:
        return "That date is not a workday. Weekend days are not PTO.", None
    if start > today:
        return "A partial day cannot be in the future. Use HOURS: full to request it.", None
    if start < today - timedelta(days=PAST_DAY_MAX_LOOKBACK_DAYS):
        return f"START can only be in the last {PAST_DAY_MAX_LOOKBACK_DAYS} days.", None
    if hours <= 0 or hours > target + 0.001:
        return f"HOURS must be more than 0 and no more than {target:g}, or full.", None
    if kind not in (LeaveType.vacation, LeaveType.sick):
        return "A partial day can be vacation or sick. Covid and holiday are full days.", None
    err = partial_pto_error(db, employee, kind.value, start, hours)
    if err:
        return err, None
    update_daily_summary(
        db, employee.id, start,
        leave_hours=hours,
        leave_type=kind.value,
        pto_approved=False,
    )
    log_action(
        db, action="email_pto", entity_type="DailySummary",
        entity_id=employee.id, employee_id=employee.id,
        new_values={"type": kind.value, "date": start.isoformat(), "hours": hours},
    )
    label = kind.value.replace("_", " ")

    def undo():
        update_daily_summary(
            db, employee.id, start, leave_hours=0, leave_type=None, pto_approved=False,
        )

    return None, (
        f"Accepted. {hours:g} hours of {label} on {start.strftime('%d %b %Y')}. "
        "Pending manager approval.",
        undo,
    )


def _finish_pto(db, message, employee, form, fields) -> bool:
    error, packed = _apply_pto(db, employee, fields)
    if error:
        return _reject(db, message, employee, "PTO was not recorded", error)
    text, undo = packed
    return _accept(db, message, employee, form, "PTO recorded", text, undo)


def _send_report(db, employee, kind: str, message) -> bool:
    if employee.role != Role.manager:
        sent = _notify(
            employee,
            "That report is for a manager",
            "Reply PHONE, OFFSITE, or PTO. A manager can also reply TIMELINE, EOD, or WEEKLY.",
            reply_message_id=message.message_id or "",
            reply_subject=message.subject or "",
        )
        if sent:
            _mark_answered(db, message)
        return sent
    from app.services.summary_email import (
        _daily_bodies, _day_facts, _people, _weekly_bodies,
    )
    from app.services.leave_sync import trim_week_leave

    today = date.today()
    people = [person for person in _people(db) if person.role != Role.supervisor]
    if kind == "timeline":
        lines = [today.strftime("%A %d %b %Y"), ""]
        for person in people:
            facts = _day_facts(db, person, today)
            lines.append(f"{facts['name']}: in {facts['in']}, out {facts['out']}")
            extra = []
            if facts["offsite"] != "—":
                extra.append(f"off site {facts['offsite']}")
            if facts["phone"] != "—":
                extra.append(f"phone {facts['phone']}h")
            if extra:
                lines.append("  " + "; ".join(extra))
            if facts["notes"]:
                lines.append(f"  {facts['notes']}")
        text = "\n".join(lines)
        sent = _send_card(
            employee, "Team timeline", today.strftime("%A %d %b"),
            ["Declared times for today. Supervisors are not on this list."],
            text, message,
        )
    elif kind == "eod":
        rows = [_day_facts(db, person, today) for person in people]
        html, text = _daily_bodies(rows, today)
        sent = _send_email(
            (employee.email or "").strip(),
            f"SDC daily summary — {today.strftime('%a %d %b')}",
            html, text,
        )
    else:
        monday = today - timedelta(days=today.weekday())
        if kind == "weekly_last":
            monday -= timedelta(days=7)
        for person in people:
            trim_week_leave(db, person.id, monday)
        people_days = []
        for person in people:
            days = []
            for offset in range(5):
                day = monday + timedelta(days=offset)
                facts = _day_facts(db, person, day)
                facts["day_name"] = day.strftime("%A")
                days.append(facts)
            people_days.append((person.name, days))
        html, text = _weekly_bodies(people_days, monday)
        end = monday + timedelta(days=4)
        sent = _send_email(
            (employee.email or "").strip(),
            f"SDC weekly summary — {monday.strftime('%d %b')} to {end.strftime('%d %b')}",
            html, text,
        )
    if sent:
        _mark_answered(db, message)
    return sent


def _punch_code(db: Session, code: str) -> bool:
    """Leave check-in, checkout, and missed-punch replies to those handlers."""
    return any((
        db.query(CheckoutToken).filter(CheckoutToken.code == code).first(),
        db.query(CheckinToken).filter(CheckinToken.code == code).first(),
        db.query(MissedEntryToken).filter(MissedEntryToken.code == code).first(),
    ))


def process_email_menu(db: Session) -> int:
    """Answer hello with a menu, and filled-in forms with a confirmation."""
    since = datetime.utcnow() - timedelta(days=4)
    messages = (
        db.query(InboundEmail)
        .filter(
            InboundEmail.fetched_at >= since,
            InboundEmail.answered == False,  # noqa: E712
        )
        .order_by(InboundEmail.id.asc())
        .limit(40)
        .all()
    )
    own = (SMTP_USER or "").strip().lower()
    done = 0
    for message in messages:
        subject = (message.subject or "").strip()
        if _AUTO_RE.search(subject):
            _mark_answered(db, message)
            continue
        sender = (message.from_addr or "").strip().lower()
        if not sender or sender == own:
            _mark_answered(db, message)
            continue
        employee = _employee_for(db, sender)
        if employee is None:
            _mark_answered(db, message)
            continue
        if not employee.is_active:
            sent = _notify(
                employee,
                "This account is not active",
                "This email is on the tracker, but the account is not active.",
                reply_message_id=message.message_id or "",
                reply_subject=subject,
            )
            if sent:
                _mark_answered(db, message)
            continue
        fresh = _fresh_reply(message.body_text or "")
        fields = _fields(message.body_text or "")
        code = (fields.get("CODE") or "").upper()
        if not code:
            found = _CODE_RE.search(message.body_text or "")
            code = found.group(1).upper() if found else ""
        form = None
        if code:
            form = db.query(EmailForm).filter(EmailForm.code == code).first()
        if form is not None:
            if _handle_form(db, message, employee, form, fields):
                done += 1
            continue
        if code and _punch_code(db, code):
            continue
        if employee.role == Role.customer:
            sent = _notify(
                employee,
                "Nothing to fill in by email",
                "A customer login watches the timeline on the site. It does not log time by email.",
                reply_message_id=message.message_id or "",
                reply_subject=subject,
            )
            if sent:
                _mark_answered(db, message)
                done += 1
            continue
        picked = _choice(fresh, subject)
        if picked in _REPORTS:
            if _send_report(db, employee, picked, message):
                done += 1
            continue
        forms = {
            "phone": (
                "Phone call",
                [
                    "Reply with these lines. Leave CODE unchanged.",
                    "DATE is the day of the call. HOURS is how long, such as 1.5 or 90m. NOTE is optional.",
                    "The confirmation can take up to 5 minutes.",
                ],
            ),
            "offsite": (
                "Off site",
                [
                    "Reply with these lines. Leave CODE unchanged.",
                    "START and END are times, such as 09:00 or 0900. Leave END blank if you are still there.",
                    "PLACE is where you went. The confirmation can take up to 5 minutes.",
                ],
            ),
            "pto": (
                "PTO",
                [
                    "Reply with these lines. Leave CODE unchanged.",
                    "TYPE is vacation, sick, covid, or holiday.",
                    "HOURS is full for a whole day, or a number such as 4.5 for part of one day.",
                    "Weekend days are not counted. The confirmation can take up to 5 minutes.",
                ],
            ),
        }
        if picked in forms:
            title, paragraphs = forms[picked]
            form = _issue_form(db, employee, picked)
            if picked == "phone":
                pre = _phone_form_text(form, date.today())
            elif picked == "offsite":
                pre = (
                    f"CODE: {form.code}\n"
                    f"DATE: {date.today().isoformat()}\n"
                    "START: 09:00\n"
                    "END:\n"
                    "PLACE:\n"
                    "NOTE:"
                )
            else:
                pre = (
                    f"CODE: {form.code}\n"
                    "TYPE: vacation\n"
                    f"START: {date.today().isoformat()}\n"
                    f"END: {date.today().isoformat()}\n"
                    "HOURS: full\n"
                    "NOTE:"
                )
            sent = _send_card(employee, title, "Form", paragraphs, pre, message)
            if sent:
                _mark_answered(db, message)
                done += 1
            else:
                db.delete(form)
                db.commit()
            continue
        title, text = _menu_text(employee, not _recognized(fresh, subject))
        sent = _notify(
            employee,
            title,
            text,
            reply_message_id=message.message_id or "",
            reply_subject=subject,
        )
        if sent:
            _mark_answered(db, message)
            done += 1
    return done
