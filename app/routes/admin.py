"""Admin routes — user management, remote authorizations."""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_employee, hash_pin
from app.models import (
    Employee, Role, RemoteAuthorization, AuthorizationStatus,
    DailySummary, TimeEntry, EntryType, LeaveRequest, LeaveStatus,
)
from app.services.audit import log_action
from app.services.time_calc import get_target_hours, update_daily_summary

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, db: Session = Depends(get_db)):
    """Admin dashboard."""
    employee = get_current_employee(request, db)
    if not employee or employee.role not in (Role.manager, Role.supervisor):
        return RedirectResponse(url="/login", status_code=303)

    employees = db.query(Employee).order_by(Employee.name).all()
    authorizations = db.query(RemoteAuthorization).filter(
        RemoteAuthorization.date >= date.today(),
    ).order_by(RemoteAuthorization.date).all()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "employee": employee,
        "employees": employees,
        "authorizations": authorizations,
        "error": None,
        "success": None,
    })


@router.post("/admin/employee", response_class=HTMLResponse)
async def add_employee(
    request: Request,
    name: str = Form(...),
    pin: str = Form(...),
    role: str = Form("employee"),
    db: Session = Depends(get_db),
):
    """Add a new employee."""
    employee = get_current_employee(request, db)
    if not employee or employee.role != Role.manager:
        return RedirectResponse(url="/login", status_code=303)

    new_emp = Employee(
        name=name,
        pin_hash=hash_pin(pin),
        role=Role(role),
        is_active=True,
    )
    db.add(new_emp)
    db.commit()

    log_action(
        db, action="add_employee", entity_type="Employee",
        entity_id=new_emp.id, employee_id=employee.id,
        new_values={"name": name, "role": role},
        ip_address=request.client.host if request.client else "",
    )

    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/authorization", response_class=HTMLResponse)
async def create_authorization(
    request: Request,
    employee_id: int = Form(...),
    auth_date: str = Form(...),
    max_hours: float = Form(...),
    location: str = Form("WFH"),
    db: Session = Depends(get_db),
):
    """Create a remote work authorization."""
    employee = get_current_employee(request, db)
    if not employee or employee.role != Role.manager:
        return RedirectResponse(url="/login", status_code=303)

    auth = RemoteAuthorization(
        employee_id=employee_id,
        authorized_by=employee.id,
        date=date.fromisoformat(auth_date),
        max_hours=max_hours,
        location=location,
        status=AuthorizationStatus.active,
    )
    db.add(auth)
    db.commit()

    log_action(
        db, action="create_authorization", entity_type="RemoteAuthorization",
        entity_id=auth.id, employee_id=employee.id,
        new_values={
            "target_employee": employee_id,
            "date": auth_date,
            "max_hours": max_hours,
            "location": location,
        },
        ip_address=request.client.host if request.client else "",
    )

    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/employee/{emp_id}/toggle")
async def toggle_employee(
    emp_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Activate/deactivate an employee."""
    employee = get_current_employee(request, db)
    if not employee or employee.role != Role.manager:
        return RedirectResponse(url="/login", status_code=303)

    target = db.query(Employee).filter(Employee.id == emp_id).first()
    if target and target.id != employee.id:
        old_val = target.is_active
        target.is_active = not target.is_active
        db.commit()

        log_action(
            db, action="toggle_employee", entity_type="Employee",
            entity_id=target.id, employee_id=employee.id,
            old_values={"is_active": old_val},
            new_values={"is_active": target.is_active},
            ip_address=request.client.host if request.client else "",
        )

    return RedirectResponse(url="/admin", status_code=303)


def _monday_of_week(d: date) -> date:
    """Return the Monday of the week containing date d."""
    return d - timedelta(days=d.weekday())


@router.get("/admin/timesheet", response_class=HTMLResponse)
async def team_timesheet(
    request: Request,
    week: str = Query(None),
    db: Session = Depends(get_db),
):
    """Show all employees' time for a given week."""
    employee = get_current_employee(request, db)
    if not employee or employee.role not in (Role.manager, Role.supervisor):
        return RedirectResponse(url="/login", status_code=303)

    # Determine the week to display
    today = date.today()
    if week:
        try:
            week_start = date.fromisoformat(week)
            week_start = _monday_of_week(week_start)
        except ValueError:
            week_start = _monday_of_week(today)
    else:
        week_start = _monday_of_week(today)

    week_end = week_start + timedelta(days=4)  # Friday
    prev_week = week_start - timedelta(days=7)
    next_week = week_start + timedelta(days=7)
    is_current_week = week_start == _monday_of_week(today)
    is_future = week_start > _monday_of_week(today)

    # Build day headers (Mon-Fri)
    days = []
    for i in range(5):
        d = week_start + timedelta(days=i)
        days.append({
            "date": d,
            "label": d.strftime("%a"),
            "full_label": d.strftime("%a %d %b"),
            "target": get_target_hours(d),
            "is_today": d == today,
        })

    # Get all active employees
    employees = db.query(Employee).filter(Employee.is_active == True).order_by(Employee.name).all()

    # Build grid: employee rows × day columns
    rows = []
    for emp in employees:
        summaries = db.query(DailySummary).filter(
            DailySummary.employee_id == emp.id,
            DailySummary.date >= week_start,
            DailySummary.date <= week_end,
        ).all()
        summary_map = {s.date: s for s in summaries}

        # Check for approved leave days
        leaves = db.query(LeaveRequest).filter(
            LeaveRequest.employee_id == emp.id,
            LeaveRequest.status == LeaveStatus.approved,
            LeaveRequest.start_date <= week_end,
            LeaveRequest.end_date >= week_start,
        ).all()
        leave_dates = set()
        for leave in leaves:
            d = max(leave.start_date, week_start)
            while d <= min(leave.end_date, week_end):
                leave_dates.add(d)
                d += timedelta(days=1)

        day_cells = []
        week_total = 0.0
        week_target = 0.0
        for day_info in days:
            d = day_info["date"]
            s = summary_map.get(d)
            target = day_info["target"]
            is_leave = d in leave_dates

            worked = s.total_hours if s else 0.0
            leave_hrs = s.leave_hours if s else 0.0
            leave_approved_flag = s.leave_approved if s else False
            leave_type_val = s.leave_type.value if s and s.leave_type else None
            compliant = s.is_compliant if s else False
            lunch_pending = (s.lunch_end_of_day and not s.lunch_approved) if s else False
            lunch_approved_flag = (s.lunch_end_of_day and s.lunch_approved) if s else False
            pto_pending = (leave_hrs > 0 and not leave_approved_flag)
            pto_approved = (leave_hrs > 0 and leave_approved_flag)

            # Effective total: only include approved PTO
            approved_leave = leave_hrs if leave_approved_flag else 0.0
            effective = worked + approved_leave
            week_total += effective
            if not is_leave:
                week_target += target

            # PTO type label
            leave_type_label = None
            if leave_type_val == "vacation":
                leave_type_label = "PTO (VAC)"
            elif leave_type_val == "sick":
                leave_type_label = "PTO (SIC)"

            # Determine cell status — pending items take priority
            if lunch_pending:
                status = "lunch_pending"
            elif pto_pending:
                status = "pto_pending"
            elif is_leave:
                status = "leave"
            elif d > today:
                status = "future"
            elif worked <= 0 and leave_hrs <= 0 and d < today:
                status = "missing"
            elif compliant:
                status = "compliant"
            else:
                status = "partial"

            day_cells.append({
                "date": d,
                "worked": worked,
                "leave_hours": leave_hrs,
                "leave_type_label": leave_type_label,
                "pto_pending": pto_pending,
                "pto_approved": pto_approved,
                "effective": round(effective, 2),
                "target": target,
                "status": status,
                "is_leave": is_leave,
                "lunch_pending": lunch_pending,
                "lunch_approved": lunch_approved_flag,
                "summary_id": s.id if s else None,
            })

        rows.append({
            "employee": emp,
            "days": day_cells,
            "week_total": round(week_total, 2),
            "week_target": week_target,
            "week_compliant": week_total >= (week_target - 0.01),
        })

    return templates.TemplateResponse("admin_timesheet.html", {
        "request": request,
        "employee": employee,
        "days": days,
        "rows": rows,
        "week_start": week_start,
        "week_end": week_end,
        "prev_week": prev_week.isoformat(),
        "next_week": next_week.isoformat(),
        "is_current_week": is_current_week,
        "is_future": is_future,
        "today": today,
    })


@router.post("/admin/approve-lunch", response_class=HTMLResponse)
async def approve_lunch(
    request: Request,
    summary_id: int = Form(...),
    week: str = Form(""),
    db: Session = Depends(get_db),
):
    """Approve a lunch-at-end-of-day deviation. Adds +1h to worked hours."""
    employee = get_current_employee(request, db)
    if not employee or employee.role not in (Role.manager, Role.supervisor):
        return RedirectResponse(url="/login", status_code=303)

    summary = db.query(DailySummary).filter(DailySummary.id == summary_id).first()
    if not summary:
        return RedirectResponse(url=f"/admin/timesheet?week={week}", status_code=303)

    # Approve and recalculate (update_daily_summary will add +1h)
    update_daily_summary(
        db, summary.employee_id, summary.date,
        lunch_end_of_day=True, lunch_approved=True,
    )

    log_action(
        db, action="approve_lunch", entity_type="DailySummary",
        entity_id=summary.id, employee_id=employee.id,
        new_values={
            "target_employee": summary.employee_id,
            "date": str(summary.date),
            "lunch_approved": True,
        },
        ip_address=request.client.host if request.client else "",
    )

    redirect_url = f"/admin/timesheet?week={week}" if week else "/admin/timesheet"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/admin/approve-pto", response_class=HTMLResponse)
async def approve_pto(
    request: Request,
    summary_id: int = Form(...),
    week: str = Form(""),
    db: Session = Depends(get_db),
):
    """Approve a PTO request. Adds leave_hours to compliance calculation."""
    employee = get_current_employee(request, db)
    if not employee or employee.role not in (Role.manager, Role.supervisor):
        return RedirectResponse(url="/login", status_code=303)

    summary = db.query(DailySummary).filter(DailySummary.id == summary_id).first()
    if not summary:
        return RedirectResponse(url=f"/admin/timesheet?week={week}", status_code=303)

    # Approve PTO and recalculate compliance
    update_daily_summary(
        db, summary.employee_id, summary.date,
        pto_approved=True,
    )

    log_action(
        db, action="approve_pto", entity_type="DailySummary",
        entity_id=summary.id, employee_id=employee.id,
        new_values={
            "target_employee": summary.employee_id,
            "date": str(summary.date),
            "leave_approved": True,
            "leave_type": summary.leave_type.value if summary.leave_type else None,
            "leave_hours": summary.leave_hours,
        },
        ip_address=request.client.host if request.client else "",
    )

    redirect_url = f"/admin/timesheet?week={week}" if week else "/admin/timesheet"
    return RedirectResponse(url=redirect_url, status_code=303)
