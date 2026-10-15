"""End-of-day and end-of-week summary mail for managers."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from html import escape

from sqlalchemy.orm import Session

from app.config import BEOD_MINIMUM_HOURS, EMAIL_ENABLED
from app.models import (
    DailySummary, Employee, EntryType, LeaveType, OffsiteEntry,
    PhoneSupportEntry, Role, TimeEntry,
)
from app.services.email import _send_email
from app.services.settings import get_bool_setting, get_setting, parse_hhmm_setting, set_setting
from app.services.time_calc import (
    BEOD_CREDIT_HOURS,
    calculate_clock_hours,
    calculate_offsite_hours,
    calculate_phone_hours,
    get_target_hours,
    resolve_beod_request,
)

logger = logging.getLogger(__name__)

# Send once, if the kiosk is awake within this long after the configured time.
_WINDOW_MINUTES = 180
_WEEKDAYS = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)
_LEAVE_LABEL = {
    LeaveType.vacation: "vacation",
    LeaveType.sick: "sick",
    LeaveType.covid_sick: "covid sick",
    LeaveType.uae_holiday: "UAE holiday",
}


def _hm(value: datetime | None) -> str:
    return value.strftime("%H:%M") if value else "—"


def _hours(value: float) -> str:
    return f"{value:.2f}"


def _people(db: Session) -> list[Employee]:
    return (
        db.query(Employee)
        .filter(Employee.is_active == True, Employee.role != Role.customer)
        .order_by(Employee.name)
        .all()
    )


def _managers(db: Session) -> list[Employee]:
    return (
        db.query(Employee)
        .filter(
            Employee.is_active == True,
            Employee.role == Role.manager,
            Employee.email != None,
            Employee.email != "",
        )
        .order_by(Employee.name)
        .all()
    )


def _day_facts(db: Session, employee: Employee, work_date: date) -> dict:
    entries = (
        db.query(TimeEntry)
        .filter(TimeEntry.employee_id == employee.id, TimeEntry.date == work_date)
        .order_by(TimeEntry.declared_time)
        .all()
    )
    offsites = (
        db.query(OffsiteEntry)
        .filter(OffsiteEntry.employee_id == employee.id, OffsiteEntry.date == work_date)
        .order_by(OffsiteEntry.start_time)
        .all()
    )
    phones = (
        db.query(PhoneSupportEntry)
        .filter(PhoneSupportEntry.employee_id == employee.id, PhoneSupportEntry.date == work_date)
        .all()
    )
    summary = (
        db.query(DailySummary)
        .filter(DailySummary.employee_id == employee.id, DailySummary.date == work_date)
        .first()
    )

    ins = [_hm(e.declared_time) for e in entries if e.entry_type == EntryType.check_in]
    outs = [_hm(e.declared_time) for e in entries if e.entry_type == EntryType.check_out]
    still_in = bool(entries) and entries[-1].entry_type == EntryType.check_in

    offsite_bits = []
    offsite_open = False
    for item in offsites:
        end = "open" if not item.end_time else _hm(item.end_time)
        if not item.end_time:
            offsite_open = True
        place = (item.location or "").strip()
        offsite_bits.append(f"{_hm(item.start_time)}–{end}" + (f" {place}" if place else ""))

    clock = calculate_clock_hours(entries)
    offsite_h = calculate_offsite_hours(offsites)
    phone_h = calculate_phone_hours(phones)
    work = round(clock + offsite_h + phone_h, 2)

    beod = 0.0
    leave_h = 0.0
    leave_label = ""
    if summary is not None:
        if summary.lunch_end_of_day and summary.lunch_approved and work >= BEOD_MINIMUM_HOURS:
            requested = resolve_beod_request(True, None, summary.beod_requested_hours)
            beod = requested if requested > 0 else BEOD_CREDIT_HOURS
        if summary.leave_approved and summary.leave_hours:
            leave_h = float(summary.leave_hours)
            leave_label = _LEAVE_LABEL.get(summary.leave_type, "leave")

    total = round(work + beod + leave_h, 2)
    target = get_target_hours(work_date)
    notes = []
    if still_in:
        notes.append("still checked in")
    if offsite_open:
        notes.append("offsite open")
    if leave_h:
        notes.append(f"{leave_label} {_hours(leave_h)}")
    if beod:
        notes.append(f"BEOD {_hours(beod)}")
    if target and total + 0.01 < target and not still_in:
        notes.append("short")

    return {
        "name": employee.name,
        "in": ", ".join(ins) if ins else "—",
        "out": ", ".join(outs) if outs else ("still in" if still_in else "—"),
        "offsite": "; ".join(offsite_bits) if offsite_bits else "—",
        "phone": _hours(phone_h) if phone_h else "—",
        "hours": total,
        "target": target,
        "notes": ", ".join(notes),
        "empty": not entries and not offsites and not phones and not leave_h,
    }


def _th(label: str) -> str:
    return (
        f'<th align="left" style="padding:6px 8px;border-bottom:2px solid #cbd5e1;'
        f'background:#0f172a;color:#fff;font-size:12px;">{escape(label)}</th>'
    )


def _td(value: str, bold: bool = False) -> str:
    weight = "font-weight:600;" if bold else ""
    return (
        f'<td style="padding:6px 8px;border-bottom:1px solid #e2e8f0;{weight}">'
        f"{escape(value)}</td>"
    )


def _daily_bodies(rows: list[dict], work_date: date) -> tuple[str, str]:
    title = work_date.strftime("%A %d %b %Y")
    head = "".join(_th(label) for label in (
        "Name", "In", "Out", "Offsite", "Phone", "Hours", "Target", "Notes",
    ))
    body_rows = []
    text_lines = [f"SDC daily summary — {title}", ""]
    present = 0
    for row in rows:
        if row["empty"] and not row["target"]:
            continue
        present += 1
        shade = "#f8fafc" if present % 2 == 0 else "#ffffff"
        cells = "".join(_td(value, bold=(key == "name")) for key, value in (
            ("name", row["name"]),
            ("in", row["in"]),
            ("out", row["out"]),
            ("offsite", row["offsite"]),
            ("phone", row["phone"]),
            ("hours", _hours(row["hours"])),
            ("target", _hours(row["target"]) if row["target"] else "—"),
            ("notes", row["notes"] or "—"),
        ))
        body_rows.append(f'<tr style="background:{shade};">{cells}</tr>')
        text_lines.append(
            f"{row['name']}: in {row['in']}; out {row['out']}; "
            f"offsite {row['offsite']}; phone {row['phone']}; "
            f"hours {_hours(row['hours'])}"
            + (f" / {_hours(row['target'])}" if row["target"] else "")
            + (f"; {row['notes']}" if row["notes"] else "")
        )
    if not body_rows:
        body_rows.append(
            '<tr><td colspan="8" style="padding:12px;color:#64748b;">No time recorded.</td></tr>'
        )
        text_lines.append("No time recorded.")
    html = (
        f'<p style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;">'
        f"<strong>SDC daily summary — {escape(title)}</strong></p>"
        f'<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;'
        f'font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#0f172a;">'
        f"<tr>{head}</tr>{''.join(body_rows)}</table>"
        f'<p style="font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#64748b;">'
        f"Hours are clock + offsite + phone + approved BEOD + approved leave.</p>"
    )
    return html, "\n".join(text_lines)


def _weekly_bodies(people_days: list[tuple[str, list[dict]]], week_start: date) -> tuple[str, str]:
    week_end = week_start + timedelta(days=4)
    title = f"{week_start.strftime('%d %b')} – {week_end.strftime('%d %b %Y')}"
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    head = _th("Name") + "".join(_th(name) for name in day_names) + _th("Total") + _th("Target")
    body_rows = []
    text_lines = [f"SDC weekly summary — {title}", ""]
    grand = 0.0
    grand_target = 0.0
    for index, (name, days) in enumerate(people_days):
        shade = "#f8fafc" if index % 2 else "#ffffff"
        total = round(sum(day["hours"] for day in days), 2)
        target = round(sum(day["target"] for day in days), 2)
        grand += total
        grand_target += target
        cells = _td(name, bold=True)
        for day in days:
            cells += _td("—" if day["empty"] else _hours(day["hours"]))
        cells += _td(_hours(total), bold=True) + _td(_hours(target))
        body_rows.append(f'<tr style="background:{shade};">{cells}</tr>')
        text_lines.append(f"{name} — {_hours(total)} / {_hours(target)}")
        for day in days:
            if day["empty"]:
                continue
            text_lines.append(
                f"  {day['day_name'][:3]} in {day['in']}; out {day['out']}"
                + ("" if day["offsite"] == "—" else f"; offsite {day['offsite']}")
                + ("" if day["phone"] == "—" else f"; phone {day['phone']}")
                + f"; {_hours(day['hours'])}"
            )
        text_lines.append("")
    foot = (
        _td("All", bold=True)
        + "".join(_td("") for _ in day_names)
        + _td(_hours(round(grand, 2)), bold=True)
        + _td(_hours(round(grand_target, 2)))
    )
    punch_html = []
    for name, days in people_days:
        lines = []
        for day in days:
            if day["empty"]:
                continue
            extra = ""
            if day["offsite"] != "—":
                extra += f"; offsite {day['offsite']}"
            if day["phone"] != "—":
                extra += f"; phone {day['phone']}"
            if day["notes"]:
                extra += f"; {day['notes']}"
            lines.append(
                f"{escape(day['day_name'][:3])} in {escape(day['in'])}; "
                f"out {escape(day['out'])}{escape(extra)}; {_hours(day['hours'])}"
            )
        if lines:
            punch_html.append(
                f'<p style="font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#0f172a;margin:8px 0 2px;">'
                f"<strong>{escape(name)}</strong></p>"
                f'<p style="font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#334155;margin:0;">'
                + "<br>".join(lines) + "</p>"
            )
    html = (
        f'<p style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;">'
        f"<strong>SDC weekly summary — {escape(title)}</strong></p>"
        f'<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;'
        f'font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#0f172a;">'
        f"<tr>{head}</tr>{''.join(body_rows)}"
        f'<tr style="background:#e2e8f0;">{foot}</tr></table>'
        + "".join(punch_html)
        + '<p style="font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#64748b;">'
        f"Each day is clock + offsite + phone + approved BEOD + approved leave.</p>"
    )
    text_lines.append(f"All — {_hours(round(grand, 2))} / {_hours(round(grand_target, 2))}")
    return html, "\n".join(text_lines)


def _deliver(db: Session, subject: str, html: str, text: str) -> bool:
    managers = _managers(db)
    if not managers:
        logger.warning("Summary not sent (%s): no manager has an email address", subject)
        return True
    if not EMAIL_ENABLED:
        logger.warning("Summary not sent (%s): SMTP is not configured", subject)
        return False
    sent = 0
    for manager in managers:
        if _send_email(manager.email.strip(), subject, html, text):
            sent += 1
    logger.info("Summary '%s' sent to %s of %s managers", subject, sent, len(managers))
    return sent > 0


def send_daily_summary(db: Session, work_date: date | None = None) -> bool:
    work_date = work_date or date.today()
    rows = [_day_facts(db, person, work_date) for person in _people(db)]
    html, text = _daily_bodies(rows, work_date)
    subject = f"SDC daily summary — {work_date.strftime('%a %d %b')}"
    return _deliver(db, subject, html, text)


def send_weekly_summary(db: Session, week_start: date | None = None) -> bool:
    today = date.today()
    if week_start is None:
        week_start = today - timedelta(days=today.weekday())
    people_days = []
    for person in _people(db):
        days = []
        for offset in range(5):
            day = week_start + timedelta(days=offset)
            facts = _day_facts(db, person, day)
            facts["day_name"] = day.strftime("%A")
            days.append(facts)
        people_days.append((person.name, days))
    html, text = _weekly_bodies(people_days, week_start)
    subject = (
        f"SDC weekly summary — {week_start.strftime('%d %b')} to "
        f"{(week_start + timedelta(days=4)).strftime('%d %b')}"
    )
    return _deliver(db, subject, html, text)


def _is_due(now: datetime, hhmm: str, last_sent: str) -> bool:
    if (last_sent or "") == now.date().isoformat():
        return False
    target = parse_hhmm_setting(hhmm, "1700")
    now_min = now.hour * 60 + now.minute
    return target <= now_min <= target + _WINDOW_MINUTES


def _week_start_for(today: date, weekday_name: str) -> date:
    monday = today - timedelta(days=today.weekday())
    # A Monday send is the recap of the week that just finished.
    if weekday_name == "monday":
        return monday - timedelta(days=7)
    return monday


def send_scheduled_summaries(db: Session, now: datetime | None = None) -> None:
    """Send the daily and weekly manager mail if the configured time has arrived."""
    now = now or datetime.now()
    today = now.date()

    if get_bool_setting(db, "summary_daily_enabled") and today.weekday() < 5:
        if _is_due(now, get_setting(db, "summary_daily_time"), get_setting(db, "summary_daily_last_sent")):
            if send_daily_summary(db, today):
                set_setting(db, "summary_daily_last_sent", today.isoformat())

    if get_bool_setting(db, "summary_weekly_enabled"):
        weekday_name = (get_setting(db, "summary_weekly_day") or "friday").strip().lower()
        if weekday_name not in _WEEKDAYS:
            weekday_name = "friday"
        if today.weekday() == _WEEKDAYS.index(weekday_name):
            if _is_due(now, get_setting(db, "summary_weekly_time"), get_setting(db, "summary_weekly_last_sent")):
                if send_weekly_summary(db, _week_start_for(today, weekday_name)):
                    set_setting(db, "summary_weekly_last_sent", today.isoformat())
