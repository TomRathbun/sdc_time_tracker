"""Employee starts by email: hello, a menu, then a form they fill in."""

from __future__ import annotations

import logging
import re
import secrets
from datetime import date, datetime, timedelta
from html import escape

from sqlalchemy.orm import Session

from app.config import PAST_DAY_MAX_LOOKBACK_DAYS, SMTP_USER
from app.models import (
    CheckinToken, CheckoutToken, EmailForm, Employee, InboundEmail,
    MissedEntryToken, PhoneSupportEntry, Role,
)
from app.services.audit import log_action
from app.services.email import _send_email
from app.services.email_checkout import _fresh_reply, _mark_answered, _notify
from app.services.email_layout import render_email
from app.services.time_calc import update_daily_summary

logger = logging.getLogger(__name__)

_FIELD_RE = re.compile(r"(?im)^\s*(CODE|DATE|HOURS|NOTE)\s*:\s*(.*?)\s*$")
_CODE_RE = re.compile(r"(?im)CODE:\s*([A-Fa-f0-9]{8})")
_AUTO_RE = re.compile(r"^(automatic reply|auto:|out of office|undeliverable|mail delivery)", re.I)
_GREETING = {
    "", "hello", "hi", "hey", "menu", "help", "start", "options", "hiya", "good morning",
}
_PHONE_WORDS = {"phone", "phone call", "call", "1"}


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
    if key in _PHONE_WORDS or key.startswith("phone"):
        return "phone"
    return "menu"


def _recognized(fresh: str, subject: str) -> bool:
    key = _clean_choice(fresh, subject)
    return key in _GREETING or key in _PHONE_WORDS or key.startswith("phone")


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


def _menu_text(unknown: bool) -> tuple[str, str]:
    lead = "That was not one of the options." if unknown else "Your email is on the tracker."
    text = (
        f"{lead} Reply with one word.\n\n"
        "PHONE\n"
        "Log a phone support call. A short form comes back to fill in.\n\n"
        "MENU\n"
        "Show this list again."
    )
    return "What do you want to do?", text


def _issue_phone_form(db: Session, employee: Employee) -> EmailForm:
    form = EmailForm(
        employee_id=employee.id,
        kind="phone",
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
    if form.kind != "phone":
        return False
    error, entry = _apply_phone(db, employee, fields)
    if error:
        sent = _notify(
            employee,
            "Phone call was not recorded",
            error + "\n\nReply again with the same CODE and the corrected line.",
            reply_message_id=message.message_id or "",
            reply_subject=message.subject or "",
        )
        if sent:
            _mark_answered(db, message)
        return sent
    form.used_at = datetime.now()
    db.commit()
    hours = _parse_hours(fields.get("HOURS", ""))
    when = _parse_date(fields.get("DATE", ""), date.today())
    sent = _notify(
        employee,
        "Phone call recorded",
        (
            f"Accepted. {hours:g} hours of phone support on {when.strftime('%d %b %Y')} are recorded.\n"
            "Email hello any time for the menu."
        ),
        reply_message_id=message.message_id or "",
        reply_subject=message.subject or "",
    )
    if sent:
        _mark_answered(db, message)
    else:
        form.used_at = None
        if entry is not None:
            db.delete(entry)
        db.commit()
        update_daily_summary(db, employee.id, entry.date if entry is not None else date.today())
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
        if picked == "phone":
            form = _issue_phone_form(db, employee)
            sent = _send_card(
                employee,
                "Phone call",
                "Form",
                [
                    "Reply with these lines. Leave CODE unchanged.",
                    "DATE is the day of the call. HOURS is how long, such as 1.5 or 90m. NOTE is optional.",
                    "The confirmation can take up to 5 minutes.",
                ],
                _phone_form_text(form, date.today()),
                message,
            )
            if sent:
                _mark_answered(db, message)
                done += 1
            else:
                db.delete(form)
                db.commit()
            continue
        title, text = _menu_text(not _recognized(fresh, subject))
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
