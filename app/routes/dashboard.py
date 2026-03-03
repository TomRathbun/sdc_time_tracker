"""Dashboard route — main landing page after login."""

from datetime import date, timedelta

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_employee
from app.models import TimeEntry, OffsiteEntry, DailySummary, LeaveRequest, EntryType, LeaveStatus
from app.services.time_calc import get_target_hours, get_weekly_summary

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

    today = date.today()
    week_start = _monday_of_week(today)

    # Today's entries
    todays_entries = db.query(TimeEntry).filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.date == today,
    ).order_by(TimeEntry.declared_time).all()

    todays_offsite = db.query(OffsiteEntry).filter(
        OffsiteEntry.employee_id == employee.id,
        OffsiteEntry.date == today,
    ).order_by(OffsiteEntry.start_time).all()

    # Daily summary
    daily_summary = db.query(DailySummary).filter(
        DailySummary.employee_id == employee.id,
        DailySummary.date == today,
    ).first()

    # Weekly summary
    weekly = get_weekly_summary(db, employee.id, week_start)

    # Pending leave requests
    pending_leaves = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee.id,
        LeaveRequest.status == LeaveStatus.pending,
    ).all()

    # Determine current status (checked in / checked out / not started)
    status = "not_started"
    if todays_entries:
        last_entry = todays_entries[-1]
        if last_entry.entry_type == EntryType.check_in:
            status = "checked_in"
        else:
            status = "checked_out"

    target_hours = get_target_hours(today)
    is_workday = target_hours > 0

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "employee": employee,
        "today": today,
        "is_workday": is_workday,
        "target_hours": target_hours,
        "status": status,
        "todays_entries": todays_entries,
        "todays_offsite": todays_offsite,
        "daily_summary": daily_summary,
        "weekly": weekly,
        "pending_leaves": pending_leaves,
        "week_start": week_start,
    })
