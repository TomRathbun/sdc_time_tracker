"""End-of-day and end-of-week summary mail for managers."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from html import escape

from sqlalchemy.orm import Session

from app.config import BEOD_MINIMUM_HOURS, EMAIL_ENABLED
from app.models import (
    DailySummary, Employee, EntryType, LeaveRequest, LeaveStatus, LeaveType,
    OffsiteEntry, PhoneSupportEntry, Role, TimeEntry,
)
from app.services.audit import log_action
from app.services.email import _send_email
from app.services.leave_balance import can_request_leave
from app.services.leave_sync import apply_leave_approval, trim_week_leave
from app.services.email_layout import render_email
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
_LEAVE_CODE = {
    LeaveType.vacation: "V",
    LeaveType.sick: "S",
    LeaveType.covid_sick: "S",
    LeaveType.uae_holiday: "H",
}


def _hm(value: datetime | None) -> str:
    return value.strftime("%H:%M") if value else "—"


def _leave_code_for(leave_type) -> str:
    if isinstance(leave_type, str):
        try:
            leave_type = LeaveType(leave_type)
        except ValueError:
            return "L"
    return _LEAVE_CODE.get(leave_type, "L")


def _pending_request(db: Session, employee_id: int, work_date: date) -> LeaveRequest | None:
    return (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status == LeaveStatus.pending,
            LeaveRequest.start_date <= work_date,
            LeaveRequest.end_date >= work_date,
        )
        .first()
    )


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
    beod_pending = False
    leave_h = 0.0
    leave_code = ""
    leave_pending = False
    if summary is not None:
        if summary.lunch_end_of_day and work >= BEOD_MINIMUM_HOURS:
            requested = resolve_beod_request(True, None, summary.beod_requested_hours)
            beod = requested if requested > 0 else BEOD_CREDIT_HOURS
            beod_pending = not bool(summary.lunch_approved)
        if summary.leave_hours:
            leave_h = float(summary.leave_hours)
            leave_code = _leave_code_for(summary.leave_type)
            leave_pending = not bool(summary.leave_approved)
    if leave_h <= 0:
        pending = _pending_request(db, employee.id, work_date)
        if pending is not None and get_target_hours(work_date) > 0:
            leave_h = get_target_hours(work_date)
            leave_code = _leave_code_for(pending.leave_type)
            leave_pending = True

    time_pending = any(not bool(getattr(entry, "offset_approved", True)) for entry in entries)
    total = round(work + beod + leave_h, 2)
    target = get_target_hours(work_date)
    notes = []
    if still_in:
        notes.append("still checked in")
    if offsite_open:
        notes.append("offsite open")
    if leave_h and leave_code:
        note = f"{leave_code}({_hours(leave_h)})"
        if leave_pending:
            note += " pending"
        notes.append(note)
    if beod:
        note = f"BEOD {_hours(beod)}"
        if beod_pending:
            note += " pending"
        notes.append(note)
    if time_pending:
        notes.append("time pending")
    if target and total + 0.01 < target and not still_in and not leave_h:
        notes.append("short")

    return {
        "name": employee.name,
        "in": ", ".join(ins) if ins else "—",
        "out": ", ".join(outs) if outs else ("still in" if still_in else "—"),
        "offsite": "; ".join(offsite_bits) if offsite_bits else "—",
        "phone": _hours(phone_h) if phone_h else "—",
        "hours": total,
        "work_hours": round(work + beod, 2),
        "leave_h": leave_h,
        "leave_code": leave_code,
        "leave_pending": leave_pending,
        "time_pending": time_pending,
        "beod_pending": beod_pending,
        "target": target,
        "notes": ", ".join(notes),
        "empty": not entries and not offsites and not phones and not leave_h,
    }


def _week_cell(day: dict) -> str:
    """A leave day reads V(9) or S(6.5). Work on that day is shown beside it."""
    mark = ""
    if day.get("leave_h") and day.get("leave_code"):
        mark = f"{day['leave_code']}({_hours(day['leave_h'])})"
        if day.get("leave_pending"):
            mark += " pending"
    worked = float(day.get("work_hours") or 0)
    if mark and worked <= 0.01:
        text = mark
    elif mark:
        text = f"{_hours(worked)} {mark}"
    elif day.get("empty"):
        text = "off" if not day.get("target") else "—"
    else:
        text = _hours(day["hours"])
    if day.get("time_pending") and text != "—":
        text += " time pending"
    if day.get("beod_pending") and text != "—":
        text += " BEOD pending"
    return text


def _th(label: str) -> str:
    return (
        '<th align="left" style="padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.08);'
        f'color:#94a3b8;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;">{escape(label)}</th>'
    )


def _td(value: str, bold: bool = False) -> str:
    weight = "font-weight:600;color:#f8fafc;" if bold else "color:#e2e8f0;"
    return (
        f'<td style="padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.06);{weight}">'
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
        shade = "#1e293b" if present % 2 == 0 else "#0f172a"
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
    html = render_email(
        f"Daily summary",
        (
            f'<table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;font-size:13px;">'
            f"<tr>{head}</tr>{''.join(body_rows)}</table>"
            '<p style="margin:16px 0 0;font-size:12px;color:#64748b;">'
            "Hours are clock + offsite + phone + approved BEOD + leave. Pending leave is counted and marked pending.</p>"
        ),
        kicker=title,
    )
    return html, "\n".join(text_lines)


def _weekly_bodies(people_days: list[tuple[str, list[dict]]], week_start: date, note: str = "") -> tuple[str, str]:
    sample = people_days[0][1] if people_days and people_days[0][1] else []
    day_names = [day["day_name"][:3] for day in sample] or ["Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]
    week_end = week_start + timedelta(days=max(len(day_names) - 1, 0))
    title = f"{week_start.strftime('%d %b')} – {week_end.strftime('%d %b %Y')}"
    head = _th("Name") + "".join(_th(name) for name in day_names) + _th("Total") + _th("Target")
    body_rows = []
    text_lines = [f"SDC weekly summary — {title}", ""]
    grand = 0.0
    grand_target = 0.0
    for index, (name, days) in enumerate(people_days):
        shade = "#1e293b" if index % 2 else "#0f172a"
        total = round(sum(day["hours"] for day in days), 2)
        target = round(sum(day["target"] for day in days), 2)
        grand += total
        grand_target += target
        cells = _td(name, bold=True)
        for day in days:
            cells += _td(_week_cell(day))
        cells += _td(_hours(total), bold=True) + _td(_hours(target))
        body_rows.append(f'<tr style="background:{shade};">{cells}</tr>')
        text_lines.append(f"{name} — {_hours(total)} / {_hours(target)}")
        for day in days:
            if day["empty"] and day.get("target"):
                continue
            if day["empty"]:
                text_lines.append(f"  {day['day_name'][:3]} off")
                continue
            text_lines.append(
                f"  {day['day_name'][:3]} {_week_cell(day)}"
                + ("" if day["in"] in ("", "—") else f" in {day['in']}")
                + ("" if day["out"] in ("", "—", "still in") else f"; out {day['out']}")
                + ("" if day["offsite"] == "—" else f"; offsite {day['offsite']}")
                + ("" if day["phone"] == "—" else f"; phone {day['phone']}")
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
            if day["empty"] and day.get("target"):
                continue
            if day["empty"]:
                lines.append(f"{escape(day['day_name'][:3])} off")
                continue
            lines.append(
                f"{escape(day['day_name'][:3])} {escape(_week_cell(day))}"
                + ("" if day["in"] in ("", "—") else f" in {escape(day['in'])}")
                + ("" if day["out"] in ("", "—", "still in") else f"; out {escape(day['out'])}")
                + ("" if day["offsite"] == "—" else f"; offsite {escape(day['offsite'])}")
                + ("" if day["phone"] == "—" else f"; phone {escape(day['phone'])}")
            )
        if lines:
            punch_html.append(
                '<p style="margin:14px 0 2px;color:#f8fafc;font-size:13px;">'
                f"<strong>{escape(name)}</strong></p>"
                '<p style="margin:0;font-size:12px;color:#94a3b8;">'
                + "<br>".join(lines) + "</p>"
            )
    html = render_email(
        "Weekly summary",
        (
            '<table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;font-size:13px;">'
            f"<tr>{head}</tr>{''.join(body_rows)}"
            f'<tr style="background:#1e293b;">{foot}</tr></table>'
            + "".join(punch_html)
            + '<p style="margin:16px 0 0;font-size:12px;color:#64748b;">'
            "Each day is office + remote site + phone + approved BEOD + leave. "
            "Saturday and Sunday are included. Pending leave is counted and marked pending.</p>"
            + (
                f'<p style="margin:12px 0 0;font-size:13px;color:#fbbf24;">{escape(note)}</p>'
                if note else ""
            )
        ),
        kicker=title,
    )
    text_lines.append(f"All — {_hours(round(grand, 2))} / {_hours(round(grand_target, 2))}")
    if note:
        text_lines.append(note)
    return html, "\n".join(text_lines)


def _deliver(db: Session, subject: str, html: str, text: str, attachments: list | None = None, kind: str = "daily_summary") -> bool:
    from app.services.email_prefs import wants_email
    managers = [person for person in _managers(db) if wants_email(person, kind)]
    if not managers:
        logger.warning("Summary not sent (%s): no manager has an email address", subject)
        return True
    if not EMAIL_ENABLED:
        logger.warning("Summary not sent (%s): SMTP is not configured", subject)
        return False
    sent = 0
    for manager in managers:
        if _send_email(manager.email.strip(), subject, html, text, attachments=attachments):
            sent += 1
    logger.info("Summary '%s' sent to %s of %s managers", subject, sent, len(managers))
    return sent > 0


def send_daily_summary(db: Session, work_date: date | None = None) -> bool:
    work_date = work_date or date.today()
    rows = [_day_facts(db, person, work_date) for person in _people(db) if person.role != Role.supervisor]
    html, text = _daily_bodies(rows, work_date)
    subject = f"SDC daily summary — {work_date.strftime('%a %d %b')}"
    return _deliver(db, subject, html, text)


def _week_is_blank(db: Session, employee: Employee, start: date, end: date) -> bool:
    """True when the week has no punches, remote site, phone, or leave."""
    if db.query(TimeEntry).filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.date >= start,
        TimeEntry.date <= end,
    ).first():
        return False
    if db.query(OffsiteEntry).filter(
        OffsiteEntry.employee_id == employee.id,
        OffsiteEntry.date >= start,
        OffsiteEntry.date <= end,
    ).first():
        return False
    if db.query(PhoneSupportEntry).filter(
        PhoneSupportEntry.employee_id == employee.id,
        PhoneSupportEntry.date >= start,
        PhoneSupportEntry.date <= end,
    ).first():
        return False
    if db.query(DailySummary).filter(
        DailySummary.employee_id == employee.id,
        DailySummary.date >= start,
        DailySummary.date <= end,
        DailySummary.leave_hours > 0,
    ).first():
        return False
    if db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee.id,
        LeaveRequest.status.in_([LeaveStatus.pending, LeaveStatus.approved]),
        LeaveRequest.start_date <= end,
        LeaveRequest.end_date >= start,
    ).first():
        return False
    return True


def fill_blank_week_pto(db: Session, week_start: date) -> list[str]:
    """Approved Monday–Friday vacation for anyone with nothing on the week.

    Returns the names that were filled. Skips a person who does not have
    enough vacation left. Supervisors are not filled.
    """
    end = week_start + timedelta(days=6)
    monday = week_start + timedelta(days=2)
    friday = week_start + timedelta(days=6)
    if get_target_hours(monday) <= 0 or get_target_hours(friday) <= 0:
        return []
    approver = _managers(db)
    approver_id = approver[0].id if approver else None
    filled = []
    for person in _people(db):
        if person.role != Role.employee:
            continue
        if not _week_is_blank(db, person, week_start, end):
            continue
        err = can_request_leave(db, person, LeaveType.vacation, monday, friday)
        if err:
            logger.warning("Blank week not filled for %s: %s", person.name, err)
            continue
        leave = LeaveRequest(
            employee_id=person.id,
            leave_type=LeaveType.vacation,
            start_date=monday,
            end_date=friday,
            status=LeaveStatus.approved,
            approved_by=approver_id,
            comments="Entered automatically before the weekly summary. The week had no time.",
        )
        db.add(leave)
        db.commit()
        db.refresh(leave)
        apply_leave_approval(db, leave)
        log_action(
            db, action="auto_blank_week_pto", entity_type="LeaveRequest",
            entity_id=leave.id, employee_id=approver_id,
            new_values={
                "target_employee": person.id,
                "start": monday.isoformat(),
                "end": friday.isoformat(),
            },
        )
        filled.append(person.name)
    return filled


def send_weekly_summary(db: Session, week_start: date | None = None) -> bool:
    from app.services.time_calc import week_start_for

    today = date.today()
    if week_start is None:
        week_start = week_start_for(today, "expat")
    filled = []
    if get_bool_setting(db, "summary_weekly_blank_pto"):
        filled = fill_blank_week_pto(db, week_start)
    people_days = []
    for person in _people(db):
        trim_week_leave(db, person.id, week_start + timedelta(days=2))
        days = []
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            facts = _day_facts(db, person, day)
            facts["day_name"] = day.strftime("%A")
            days.append(facts)
        people_days.append((person.name, days))
    html, text = _weekly_bodies(
        people_days,
        week_start,
        note=("Approved vacation was added for a blank week: " + ", ".join(filled) + ".") if filled else "",
    )
    subject = (
        f"SDC weekly summary — {week_start.strftime('%d %b')} to "
        f"{(week_start + timedelta(days=6)).strftime('%d %b')}"
    )
    return _deliver(db, subject, html, text, kind="weekly_summary")


def send_reconciliation(db: Session, through: date, weeks: int, *, show_all_pto: bool = True) -> tuple[bool, str]:
    """Email managers the quarterly spreadsheet, for one week or several."""
    from app.services.fosc_export import build_fosc_weeks_workbook

    count = max(1, min(int(weeks or 1), 8))
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    last_done = this_monday if today.weekday() > 4 else this_monday - timedelta(days=7)
    end = through - timedelta(days=through.weekday())
    if end > last_done:
        end = last_done
    starts = [end - timedelta(days=7 * (count - 1 - i)) for i in range(count)]

    for week_start in starts:
        for person in _people(db):
            trim_week_leave(db, person.id, week_start)

    book = build_fosc_weeks_workbook(db, starts, show_all_pto=show_all_pto)
    filename = f"FOSC_Weekly_{starts[0].isoformat()}_{(end + timedelta(days=4)).isoformat()}.xlsx"
    span = f"{starts[0].strftime('%d %b')} – {(end + timedelta(days=4)).strftime('%d %b %Y')}"
    html = render_email(
        "Lockheed reconciliation",
        (
            f"<p style=\"margin:0 0 12px;\">The weekly attendance sheet for <strong style=\"color:#f8fafc;\">{escape(span)}</strong> is attached.</p>"
            "<p style=\"margin:0;\">Each week is one sheet: declared in and out, BEOD, FOSC hours, PTO, and the overall total.</p>"
        ),
        kicker=span,
    )
    text = (
        f"SDC weekly reconciliation {span}.\n"
        f"The Excel file {filename} is attached. "
        "One sheet per week, with FOSC, BEOD, and PTO."
    )
    subject = f"SDC weekly time record — {span}"
    if not _managers(db):
        return False, "No manager has an email address."
    if not EMAIL_ENABLED:
        return False, "SMTP is not configured."
    if not _deliver(db, subject, html, text, [(filename, book.getvalue())], kind="reports"):
        return False, "The email was not sent. A manager needs an email address, and SMTP has to be on."
    return True, f"Sent the Excel workbook for {count} week(s), {span}, to the managers."


def send_quarter_report(db: Session, year: int, quarter: int) -> tuple[bool, str]:
    """Email managers the quarterly FOSC and PTO totals."""
    from app.services.fosc_export import build_fosc_quarterly_workbook

    if quarter < 1 or quarter > 4:
        return False, "Pick a quarter from 1 to 4."
    book = build_fosc_quarterly_workbook(db, year, quarter)
    filename = f"FOSC_Q{quarter}_{year}.xlsx"
    html = render_email(
        f"Q{quarter} {year} totals",
        (
            f"<p style=\"margin:0 0 12px;\">The quarterly totals for <strong style=\"color:#f8fafc;\">Q{quarter} {year}</strong> are attached.</p>"
            "<p style=\"margin:0;\">Each person has FOSC hours, BEOD, and PTO, with a team sum at the bottom.</p>"
        ),
        kicker=f"Q{quarter} {year}",
    )
    text = f"SDC quarterly totals Q{quarter} {year}. The Excel file {filename} is attached."
    subject = f"SDC quarterly totals — Q{quarter} {year}"
    if not _managers(db):
        return False, "No manager has an email address."
    if not EMAIL_ENABLED:
        return False, "SMTP is not configured."
    if not _deliver(db, subject, html, text, [(filename, book.getvalue())], kind="reports"):
        return False, "The email was not sent. A manager needs an email address, and SMTP has to be on."
    return True, f"Sent the Q{quarter} {year} totals to the managers."


def _is_due(now: datetime, hhmm: str, last_sent: str) -> bool:
    if (last_sent or "") == now.date().isoformat():
        return False
    target = parse_hhmm_setting(hhmm, "1700")
    now_min = now.hour * 60 + now.minute
    return target <= now_min <= target + _WINDOW_MINUTES


def _week_start_for(today: date, weekday_name: str) -> date:
    from app.services.time_calc import week_start_for

    # Expat week is Saturday–Friday. A Monday send is the week that just finished.
    start = week_start_for(today, "expat")
    if weekday_name == "monday":
        return start - timedelta(days=7)
    return start


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
