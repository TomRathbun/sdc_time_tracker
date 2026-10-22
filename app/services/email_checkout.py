"""One-time checkout codes mailed before the projected checkout.

The employee replies with the code and a declared time. The inbox poll
records that reply as the official check-out.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, time, timedelta
from html import escape

from sqlalchemy.orm import Session

from app.models import (
    CheckoutToken, Employee, EntryType, InboundEmail, LocationType, TimeEntry,
)
from app.services.audit import log_action
from app.services.email import _send_email
from app.services.email_layout import render_email
from app.services.settings import get_bool_setting
from app.services.time_calc import (
    beod_offered_on,
    format_beod_length,
    normalize_beod_length,
    remember_beod_pref,
    update_daily_summary,
)
from app.services.time_state import can_check_out

logger = logging.getLogger(__name__)

_CODE_RE = re.compile(r"(?im)^\s*CODE:\s*([A-Fa-f0-9]{8})\s*$")
_TIME_RE = re.compile(r"(?im)^\s*DECLARED\s*TIME:\s*(\d{1,2}):(\d{2})\s*$")
_BEOD_RE = re.compile(r"(?im)^\s*BEOD:\s*(yes|no)\s*$")
_BEOD_LEN_RE = re.compile(r"(?im)^\s*BEOD\s*LENGTH:\s*(\S+)\s*$")
_TOP_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})[:.](\d{2})(?!\d)")


def _fresh_reply(body: str) -> str:
    """The text the employee typed, before the quoted checkout mail."""
    kept = []
    for line in body.splitlines():
        raw = line.strip()
        if raw.startswith(">"):
            break
        if re.match(r"^_{5,}$", raw):
            break
        if re.match(r"^-{2,}", raw) and "original" in raw.lower():
            break
        if re.match(r"^on .+ wrote:?$", raw, re.I):
            break
        if re.match(r"^from:\s", raw, re.I):
            break
        kept.append(line)
    return "\n".join(kept)


def _is_new_attempt(body: str) -> bool:
    """True when the employee typed a time or left the CODE in their own text.

    A quoted copy of the original mail is not a new reply. That was sending
    the same rejection again on every inbox check.
    """
    fresh = _fresh_reply(body or "")
    if _CODE_RE.search(fresh) or re.search(r"CODE:\s*[A-Fa-f0-9]{8}", fresh, re.I):
        return True
    return _TOP_TIME_RE.search(fresh) is not None


def _already_answered(message) -> bool:
    return bool(getattr(message, "answered", False))


def _mark_answered(db: Session, message) -> None:
    message.answered = True
    db.commit()


def issue_checkout_token(db: Session, employee: Employee, checkin: TimeEntry, expected: datetime) -> CheckoutToken | None:
    """Create a code for this open check-in. None if one is already unused."""
    existing = (
        db.query(CheckoutToken)
        .filter(
            CheckoutToken.checkin_entry_id == checkin.id,
            CheckoutToken.used_at == None,
        )
        .first()
    )
    if existing:
        return None
    token = CheckoutToken(
        employee_id=employee.id,
        checkin_entry_id=checkin.id,
        work_date=checkin.date,
        code=secrets.token_hex(4).upper(),
        expected_time=expected,
        expires_at=datetime.combine(checkin.date + timedelta(days=1), time(12, 0)),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token


def cancel_checkout_token(db: Session, token: CheckoutToken) -> None:
    db.delete(token)
    db.commit()


def parse_checkout_reply(body: str) -> dict | None:
    """Pull the reply fields. None when this message is not a checkout reply."""
    if not body or "CODE:" not in body.upper():
        return None
    fresh = _fresh_reply(body)
    lines = []
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(">"):
            stripped = stripped[1:].lstrip()
        lines.append(stripped)
    full = "\n".join(lines)
    code = _CODE_RE.search(full) or re.search(r"CODE:\s*([A-Fa-f0-9]{8})", full, re.I)
    if not code:
        return None
    top = _TOP_TIME_RE.search(fresh)
    clock = None if top else (
        _TIME_RE.search(full) or re.search(r"DECLARED\s*TIME:\s*(\d{1,2}):(\d{2})", full, re.I)
    )
    picked = top or clock
    if not picked:
        return None
    hour, minute = int(picked.group(1)), int(picked.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return {"code": code.group(1).upper(), "bad_time": True}
    beod_match = _BEOD_RE.search(full)
    length_match = _BEOD_LEN_RE.search(full)
    return {
        "code": code.group(1).upper(),
        "hour": hour,
        "minute": minute,
        "beod": bool(beod_match and beod_match.group(1).lower() == "yes"),
        "beod_length": length_match.group(1) if length_match else "",
    }


def _notify(employee: Employee, subject: str, text: str, reply_message_id: str = "", reply_subject: str = "") -> bool:
    address = (employee.email or "").strip()
    if not address:
        logger.error("Checkout mail not sent: %s has no email", employee.name)
        return False
    title = subject
    message_id = (reply_message_id or "").strip()
    if message_id and not message_id.startswith("<"):
        message_id = f"<{message_id.strip('<>')}>"
    if message_id:
        base = (reply_subject or subject).strip()
        subject = base if base.lower().startswith("re:") else f"Re: {base}"
    html = render_email(
        title,
        "".join(
            f'<p style="margin:0 0 10px;">{escape(line) if line else "&nbsp;"}</p>'
            for line in text.splitlines()
        ) or "<p></p>",
        kicker="SDC Time Tracker",
    )
    ok = _send_email(address, subject, html, text, in_reply_to=message_id)
    if ok:
        logger.info("Checkout reply mailed to %s: %s", address, subject)
    else:
        logger.error("Checkout reply was not mailed to %s", address)
    return ok


def _apply(db: Session, token: CheckoutToken, employee: Employee, parsed: dict, now: datetime, reply_at: datetime) -> str | None:
    """Record the checkout. Return an error string, or None on success."""
    declared = datetime.combine(token.work_date, time(parsed["hour"], parsed["minute"]))
    state_err = can_check_out(db, employee.id, token.work_date, declared_time=declared)
    if state_err:
        return f"Rejected because {state_err}"
    reply_gap = abs((declared - reply_at).total_seconds()) / 60
    if reply_gap > 15:
        earliest = (reply_at - timedelta(minutes=15)).strftime("%H:%M")
        latest = (reply_at + timedelta(minutes=15)).strftime("%H:%M")
        return (
            f"Rejected because {declared.strftime('%H:%M')} is more than 15 minutes from "
            f"when you sent this reply ({reply_at.strftime('%H:%M')}). "
            "The declared time has to be within 15 minutes of the time you send the email. "
            f"A reply sent at {reply_at.strftime('%H:%M')} can use {earliest} to {latest}. "
            "It cannot be sent at 15:00 with a declared time of 16:00. "
            "Reply again with the same CODE and a time in that window."
        )
    early = (token.expected_time - declared).total_seconds() / 60
    if early > 15:
        return (
            f"Rejected because {declared.strftime('%H:%M')} is more than 15 minutes earlier than "
            f"the projected checkout ({token.expected_time.strftime('%H:%M')}). "
            "The reply can move the checkout up to 15 minutes earlier than the projection. "
            "A later time is allowed only if you send the reply within 15 minutes of that time. "
            "Use the kiosk to leave earlier than that. Reply again with the same CODE if the time was a mistake."
        )

    claim_beod = bool(parsed.get("beod")) and beod_offered_on(token.work_date)
    requested = None
    if claim_beod:
        requested = normalize_beod_length(parsed.get("beod_length") or employee.beod_pref_hours or 1)

    comment = "Email check-out"
    if claim_beod:
        comment += f" + BEOD {format_beod_length(requested or 1)}"

    entry = TimeEntry(
        employee_id=employee.id,
        date=token.work_date,
        declared_time=declared,
        submission_time=now,
        entry_type=EntryType.check_out,
        location_type=LocationType.office,
        is_remote=False,
        comments=comment,
        offset_approved=True,
    )
    db.add(entry)
    db.commit()

    if beod_offered_on(token.work_date):
        remember_beod_pref(employee, claim_beod, requested if claim_beod else None, token.work_date)
    beod_approved = claim_beod and get_bool_setting(db, "beod_blanket_approval")
    summary = update_daily_summary(
        db, employee.id, token.work_date,
        lunch_end_of_day=claim_beod,
        lunch_approved=beod_approved,
        beod_requested_hours=requested,
    )
    token.used_at = now
    db.commit()
    log_action(
        db, action="email_checkout", entity_type="TimeEntry",
        entity_id=entry.id, employee_id=employee.id,
        new_values={
            "declared_time": str(declared),
            "submission_time": str(now),
            "code": token.code,
            "beod": claim_beod,
        },
    )
    hours = f"{summary.total_hours:.2f}" if summary else ""
    return declared, hours


def process_checkout_replies(db: Session) -> int:
    """Apply new inbox replies that carry a checkout code. Returns how many checked out."""
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
        token = db.query(CheckoutToken).filter(CheckoutToken.code == parsed["code"]).first()
        if token is None or _already_answered(message):
            continue
        employee = db.query(Employee).filter(Employee.id == token.employee_id).first()
        if employee is None:
            continue
        sender = (message.from_addr or "").strip().lower()
        owner = (employee.email or "").strip().lower()
        subject = (message.subject or "").lower()
        if subject.startswith("automatic reply") or "out of office" in subject or subject.startswith("undeliverable"):
            _mark_answered(db, message)
            continue
        if not _is_new_attempt(message.body_text or ""):
            _mark_answered(db, message)
            continue
        if not owner or sender != owner:
            logger.warning("Checkout code %s ignored from %s", token.code, sender or "(blank)")
            _mark_answered(db, message)
            continue

        fail = None
        if parsed.get("bad_time"):
            fail = "DECLARED TIME must look like 16:30."
        elif token.used_at is not None:
            fail = f"Code {token.code} was already used."
        elif datetime.now() > token.expires_at:
            fail = f"Code {token.code} has expired. Check out on the kiosk."
        else:
            try:
                reply_at = message.received_at or datetime.now()
                result = _apply(db, token, employee, parsed, datetime.now(), reply_at)
            except Exception as exc:
                db.rollback()
                logger.exception("Email checkout failed")
                fail = f"Could not record the check-out. {exc}"
                result = None
            if isinstance(result, str):
                fail = result
            elif result is not None:
                _declared, hours = result
                when = f"{parsed['hour']:02d}:{parsed['minute']:02d}"
                _notify(
                    employee,
                    f"Checked out at {when}",
                    f"Accepted. You are checked out at {when}.\nHours recorded: {hours}.",
                    reply_message_id=message.message_id or "",
                    reply_subject=message.subject or "",
                )
                token.notified_uid = message.imap_uid
                _mark_answered(db, message)
                done += 1
                continue
        if fail:
            _notify(
                employee,
                "Check-out reply was not recorded",
                fail + "\n\nThe CODE stays the same if you reply again.",
                reply_message_id=message.message_id or "",
                reply_subject=message.subject or "",
            )
            token.notified_uid = message.imap_uid
            _mark_answered(db, message)
    return done
