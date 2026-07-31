"""Dashboard route — main landing page after login."""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_employee
from app.models import (
    TimeEntry, OffsiteEntry, PhoneSupportEntry, DailySummary,
    LeaveRequest, EntryType, LeaveStatus,
)
from app.services.time_calc import (
    get_target_hours, get_weekly_summary,
    calculate_clock_hours, calculate_offsite_hours, calculate_phone_hours,
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
