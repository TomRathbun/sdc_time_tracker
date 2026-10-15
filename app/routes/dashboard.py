"""Dashboard route — main landing page after login."""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_employee
from app.models import (
    Employee, TimeEntry, OffsiteEntry, PhoneSupportEntry, DailySummary,
    LeaveRequest, EntryType, LeaveStatus, Role,
)
from app.services.time_calc import (
    get_target_hours, get_weekly_summary,
    calculate_clock_hours, calculate_offsite_hours, calculate_phone_hours,
    projected_checkout_from_entries, format_beod_length,
)
from app.services.leave_balance import get_leave_balance

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _monday_of_week(d: date) -> date:
    """Return the Monday of the week containing date d."""
    return d - timedelta(days=d.weekday())


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Main dashboard showing today's status and weekly overview."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)
    if employee.role == Role.customer:
        return RedirectResponse(url="/admin/timesheet", status_code=303)

    now = datetime.now()
    today = now.date()
    week_start = _monday_of_week(today)

    todays_entries = db.query(TimeEntry).filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.date == today,
    ).order_by(TimeEntry.declared_time).all()

    todays_offsite = db.query(OffsiteEntry).filter(
        OffsiteEntry.employee_id == employee.id,
        OffsiteEntry.date == today,
    ).order_by(OffsiteEntry.start_time).all()

    todays_phone = db.query(PhoneSupportEntry).filter(
        PhoneSupportEntry.employee_id == employee.id,
        PhoneSupportEntry.date == today,
    ).all()

    daily_summary = db.query(DailySummary).filter(
        DailySummary.employee_id == employee.id,
        DailySummary.date == today,
    ).first()

    weekly = get_weekly_summary(db, employee.id, week_start)

    pending_leaves = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee.id,
        LeaveRequest.status == LeaveStatus.pending,
    ).all()

    upcoming_leave = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee.id,
        LeaveRequest.status == LeaveStatus.approved,
        LeaveRequest.end_date >= today,
    ).order_by(LeaveRequest.start_date).limit(5).all()

    leave_balance = get_leave_balance(db, employee)

    status = "not_started"
    if todays_entries:
        last_entry = todays_entries[-1]
        if last_entry.entry_type == EntryType.check_in:
            status = "checked_in"
        else:
            status = "checked_out"

    target_hours = get_target_hours(today)
    is_workday = target_hours > 0
    projected = projected_checkout_from_entries(
        todays_entries,
        today,
        beod_claimed=bool(daily_summary and daily_summary.lunch_end_of_day),
    ) if status == "checked_in" else None
    projected_checkout = projected.standard if projected else None
    projected_checkout_beod = projected.beod if projected else None
    projected_beod_claimed = projected.beod_claimed if projected else False

    # Live FOSC breakdown (includes open check-in session so Progress is not stuck at 0)
    clock_hours = calculate_clock_hours(todays_entries)
    live_session_hours = 0.0
    if status == "checked_in" and todays_entries:
        open_ci = todays_entries[-1]
        if open_ci.entry_type == EntryType.check_in:
            live_session_hours = max(
                0.0,
                (now - open_ci.declared_time).total_seconds() / 3600.0,
            )
            clock_hours = round(clock_hours + live_session_hours, 2)

    offsite_hours = calculate_offsite_hours(todays_offsite)
    phone_hours = calculate_phone_hours(todays_phone)
    beod_hours = float(daily_summary.beod_hours or 0) if daily_summary else 0.0
    fosc_hours = round(clock_hours + offsite_hours + phone_hours + beod_hours, 2)

    leave_hours = 0.0
    leave_approved = False
    leave_type_label = None
    if daily_summary and daily_summary.leave_hours:
        leave_hours = daily_summary.leave_hours
        leave_approved = bool(daily_summary.leave_approved)
        if daily_summary.leave_type:
            lt = daily_summary.leave_type.value
            leave_type_label = {
                "vacation": "Vacation",
                "sick": "Sick",
                "covid_sick": "COVID Sick",
                "uae_holiday": "UAE Holiday",
            }.get(lt, lt.replace("_", " ").title())

    approved_leave = leave_hours if leave_approved else 0.0
    effective_hours = round(fosc_hours + approved_leave, 2)
    progress_pct = 0.0
    if target_hours > 0:
        progress_pct = min(100.0, round((effective_hours / target_hours) * 100, 1))

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "employee": employee,
        "today": today,
        "now": now,
        "is_workday": is_workday,
        "target_hours": target_hours,
        "status": status,
        "projected_checkout": projected_checkout,
        "projected_checkout_beod": projected_checkout_beod,
        "projected_beod_claimed": projected_beod_claimed,
        "todays_entries": todays_entries,
        "todays_offsite": todays_offsite,
        "todays_phone": todays_phone,
        "daily_summary": daily_summary,
        "weekly": weekly,
        "pending_leaves": pending_leaves,
        "upcoming_leave": upcoming_leave,
        "leave_balance": leave_balance,
        "week_start": week_start,
        # Live progress (always present for template)
        "clock_hours": clock_hours,
        "live_session_hours": round(live_session_hours, 2),
        "offsite_hours": offsite_hours,
        "phone_hours": phone_hours,
        "beod_hours": beod_hours,
        "fosc_hours": fosc_hours,
        "leave_hours": leave_hours,
        "leave_approved": leave_approved,
        "leave_type_label": leave_type_label,
        "effective_hours": effective_hours,
        "progress_pct": progress_pct,
    })


_LEAVE_LABELS = {
    "vacation": "Vacation",
    "sick": "Sick",
    "covid_sick": "COVID Sick",
    "uae_holiday": "UAE Holiday",
}


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


@router.get("/my-time", response_class=HTMLResponse)
async def my_time_report(
    request: Request,
    week: str = Query(None),
    employee_id: int = Query(None),
    db: Session = Depends(get_db),
):
    """One person's punches and totals for a past or current week."""
    viewer = get_current_employee(request, db)
    if not viewer:
        return RedirectResponse(url="/login", status_code=303)
    if viewer.role == Role.customer:
        return RedirectResponse(url="/admin/timesheet", status_code=303)

    can_pick = viewer.role in (Role.manager, Role.supervisor)
    staff = []
    subject = viewer
    if can_pick:
        staff = (
            db.query(Employee)
            .filter(Employee.is_active == True, Employee.role != Role.customer)  # noqa: E712
            .order_by(Employee.name)
            .all()
        )
        if employee_id:
            picked = next((e for e in staff if e.id == employee_id), None)
            if picked:
                subject = picked

    today = date.today()
    current_monday = _monday_of(today)
    week_start = current_monday
    if week:
        try:
            week_start = _monday_of(date.fromisoformat(week))
        except ValueError:
            week_start = current_monday
    if week_start > current_monday:
        week_start = current_monday
    week_end = week_start + timedelta(days=4)
    prev_week = week_start - timedelta(days=7)
    next_week = week_start + timedelta(days=7)

    entries = (
        db.query(TimeEntry)
        .filter(
            TimeEntry.employee_id == subject.id,
            TimeEntry.date >= week_start,
            TimeEntry.date <= week_end,
        )
        .order_by(TimeEntry.declared_time)
        .all()
    )
    offsites = (
        db.query(OffsiteEntry)
        .filter(
            OffsiteEntry.employee_id == subject.id,
            OffsiteEntry.date >= week_start,
            OffsiteEntry.date <= week_end,
        )
        .order_by(OffsiteEntry.start_time)
        .all()
    )
    phones = (
        db.query(PhoneSupportEntry)
        .filter(
            PhoneSupportEntry.employee_id == subject.id,
            PhoneSupportEntry.date >= week_start,
            PhoneSupportEntry.date <= week_end,
        )
        .all()
    )
    summaries = (
        db.query(DailySummary)
        .filter(
            DailySummary.employee_id == subject.id,
            DailySummary.date >= week_start,
            DailySummary.date <= week_end,
        )
        .all()
    )
    by_entry = {}
    for e in entries:
        by_entry.setdefault(e.date, []).append(e)
    by_off = {}
    for e in offsites:
        by_off.setdefault(e.date, []).append(e)
    by_phone = {}
    for e in phones:
        by_phone.setdefault(e.date, []).append(e)
    by_sum = {s.date: s for s in summaries}

    now = datetime.now()
    days = []
    totals = {
        "clock": 0.0, "offsite": 0.0, "phone": 0.0, "beod": 0.0,
        "fosc": 0.0, "leave": 0.0, "effective": 0.0, "target": 0.0,
    }
    for i in range(5):
        d = week_start + timedelta(days=i)
        day_entries = by_entry.get(d, [])
        day_off = by_off.get(d, [])
        day_phone = by_phone.get(d, [])
        summary = by_sum.get(d)
        target = get_target_hours(d)

        clock = calculate_clock_hours(day_entries)
        if d == today and day_entries and day_entries[-1].entry_type == EntryType.check_in:
            clock = round(clock + max(0.0, (now - day_entries[-1].declared_time).total_seconds() / 3600.0), 2)
        off_h = round(calculate_offsite_hours(day_off), 2)
        phone_h = round(calculate_phone_hours(day_phone), 2)
        beod = float(summary.beod_hours or 0) if summary else 0.0
        # Past days use the stored FOSC total so approvals match the timesheet.
        if summary and d != today:
            clock = float(summary.clock_hours or 0)
            off_h = float(summary.offsite_hours or 0)
            phone_h = float(summary.phone_hours or 0)
            fosc = float(summary.total_hours or 0)
        else:
            fosc = round(clock + off_h + phone_h + beod, 2)

        leave_h = float(summary.leave_hours or 0) if summary else 0.0
        leave_ok = bool(summary and summary.leave_approved and leave_h)
        leave_label = ""
        if summary and summary.leave_type and leave_h:
            lt = summary.leave_type.value
            leave_label = _LEAVE_LABELS.get(lt, lt.replace("_", " ").title())
        effective = round(fosc + (leave_h if leave_ok else 0.0), 2)
        beod_label = ""
        if summary and (summary.beod_hours or summary.lunch_end_of_day):
            beod_label = format_beod_length(summary.beod_hours or summary.beod_requested_hours or 1.0)

        days.append({
            "date": d,
            "is_today": d == today,
            "target": target,
            "entries": day_entries,
            "offsites": day_off,
            "phones": day_phone,
            "clock": round(clock, 2),
            "offsite": round(off_h, 2),
            "phone": round(phone_h, 2),
            "beod": round(beod, 2),
            "beod_label": beod_label,
            "beod_pending": bool(summary and summary.lunch_end_of_day and not summary.lunch_approved),
            "fosc": round(fosc, 2),
            "leave": leave_h,
            "leave_ok": leave_ok,
            "leave_label": leave_label,
            "effective": effective,
            "compliant": effective + 0.01 >= target if target else True,
        })
        totals["clock"] += clock
        totals["offsite"] += off_h
        totals["phone"] += phone_h
        totals["beod"] += beod
        totals["fosc"] += fosc
        totals["leave"] += leave_h if leave_ok else 0.0
        totals["effective"] += effective
        totals["target"] += target

    for key in totals:
        totals[key] = round(totals[key], 2)

    def _href(monday: date, emp_id: int) -> str:
        url = f"/my-time?week={monday.isoformat()}"
        if can_pick:
            url += f"&employee_id={emp_id}"
        return url

    return templates.TemplateResponse("my_time.html", {
        "request": request,
        "employee": viewer,
        "subject": subject,
        "staff": staff,
        "can_pick": can_pick,
        "days": days,
        "totals": totals,
        "week_start": week_start,
        "week_end": week_end,
        "is_current_week": week_start == current_monday,
        "prev_url": _href(prev_week, subject.id),
        "next_url": _href(next_week, subject.id),
        "week_compliant": totals["effective"] + 0.01 >= totals["target"],
    })
