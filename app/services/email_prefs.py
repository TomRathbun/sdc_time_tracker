"""Per-person email opt-in. Missing keys stay on, so existing people keep every mail."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import Employee, Role

# audience "lead" is a manager or supervisor. Everyone else only sees "all".
EMAIL_KINDS = (
    ("checkin", "Check-in confirmation", "all", "The note after you check in, with the week so far."),
    ("checkin_reminder", "Morning check-in", "all", "Fifteen minutes before your usual start, with a reply code."),
    ("checkout_reminder", "Checkout reminder", "all", "Fifteen minutes before you leave, with a reply code."),
    ("missed_entry", "Missed punches", "all", "The daily list of open punches and how to reply."),
    ("policy", "Policy notice", "all", "When a punch is outside the allowed time window."),
    ("features", "Feature announcements", "all", "Notes a manager sends about tracker changes."),
    ("daily_summary", "Daily team summary", "lead", "End-of-day check-ins and check-outs for the team."),
    ("weekly_summary", "Weekly team summary", "lead", "Friday hours for the team."),
    ("reports", "Customer reports", "lead", "The weekly attendance sheet and the quarterly totals."),
    ("admin_notice", "Timesheet changes", "lead", "When a past day is approved or changed."),
)

_KEYS = {item[0] for item in EMAIL_KINDS}


def load_prefs(employee) -> dict:
    raw = getattr(employee, "email_prefs", None) or ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: bool(value) for key, value in data.items() if key in _KEYS}


def wants_email(employee, kind: str) -> bool:
    """True unless this person has turned that kind off."""
    if kind not in _KEYS:
        return True
    prefs = load_prefs(employee)
    if kind not in prefs:
        return True
    return bool(prefs[kind])


def kinds_for(employee) -> list[dict]:
    lead = getattr(employee, "role", None) in (Role.manager, Role.supervisor)
    prefs = load_prefs(employee)
    rows = []
    for key, label, audience, detail in EMAIL_KINDS:
        if audience == "lead" and not lead:
            continue
        rows.append({
            "key": key,
            "label": label,
            "detail": detail,
            "audience": audience,
            "on": prefs.get(key, True),
        })
    return rows


def save_prefs(employee, shown: list[str], chosen: set[str]) -> None:
    """Update only the kinds this form showed. Anything hidden stays as it was."""
    prefs = load_prefs(employee)
    for key in shown:
        if key in _KEYS:
            prefs[key] = key in chosen
    employee.email_prefs = json.dumps(prefs)


def recipient_emails(db: Session, kind: str, *, role: Role = Role.manager) -> list[str]:
    people = (
        db.query(Employee)
        .filter(
            Employee.is_active == True,  # noqa: E712
            Employee.role == role,
            Employee.email != None,  # noqa: E711
            Employee.email != "",
        )
        .all()
    )
    return [person.email.strip() for person in people if wants_email(person, kind)]
