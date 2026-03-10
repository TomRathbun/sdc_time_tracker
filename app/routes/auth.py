"""Authentication routes — PIN login / logout."""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import verify_pin, create_session_token, get_current_employee
from app.config import SESSION_COOKIE_NAME
from app.models import Employee, TimeEntry, EntryType, LeaveRequest, LeaveStatus, DailySummary
from app.services.audit import log_action

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _get_employee_status(db: Session, employees):
    """Build a dict of employee_id → {status, time, minutes} for today's entries."""
    today = date.today()
    status_map = {}
    for emp in employees:
        entries = db.query(TimeEntry).filter(
            TimeEntry.employee_id == emp.id,
            TimeEntry.date == today,
        ).order_by(TimeEntry.declared_time).all()
        if not entries:
            status_map[emp.id] = {"status": "not_started", "time": None, "minutes": None}
        elif entries[-1].entry_type == EntryType.check_in:
            last_time = entries[-1].declared_time
            status_map[emp.id] = {
                "status": "checked_in",
                "time": last_time.strftime("%H:%M"),
                "minutes": last_time.hour * 60 + last_time.minute
            }
        else:
            last_time = entries[-1].declared_time
            status_map[emp.id] = {
                "status": "checked_out",
                "time": last_time.strftime("%H:%M"),
                "minutes": last_time.hour * 60 + last_time.minute
            }
    return status_map


def _get_avg_times(db: Session, employees):
    """Compute average check-in and check-out times (as minutes since midnight)
    per employee from the last 30 days of history.
    Returns dict: employee_id → {"avg_checkin": float|None, "avg_checkout": float|None}
    """
    cutoff = date.today() - timedelta(days=30)
    result = {}
    for emp in employees:
        entries = db.query(TimeEntry).filter(
            TimeEntry.employee_id == emp.id,
            TimeEntry.date >= cutoff,
            TimeEntry.date < date.today(),   # exclude today
        ).all()

        ci_minutes = []
        co_minutes = []
        for e in entries:
            mins = e.declared_time.hour * 60 + e.declared_time.minute
            if e.entry_type == EntryType.check_in:
                ci_minutes.append(mins)
            else:
                co_minutes.append(mins)

        result[emp.id] = {
            "avg_checkin": sum(ci_minutes) / len(ci_minutes) if ci_minutes else None,
            "avg_checkout": sum(co_minutes) / len(co_minutes) if co_minutes else None,
        }
    return result


def _is_on_leave_today(db: Session, emp_id: int) -> bool:
    """Check if an employee has approved full-day leave today."""
    today = date.today()
    leave = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == emp_id,
        LeaveRequest.start_date <= today,
        LeaveRequest.end_date >= today,
        LeaveRequest.status == LeaveStatus.approved,
    ).first()
    if leave:
        return True
    # Also check DailySummary for full-day leave
    summary = db.query(DailySummary).filter(
        DailySummary.employee_id == emp_id,
        DailySummary.date == today,
        DailySummary.leave_hours >= 8,
    ).first()
    return summary is not None


def _smart_sort_employees(employees, status_map, avg_times, on_leave_map, now_hour):
    """Sort employee list intelligently.

    Tiers (lower = higher in list):
      0 — Not checked in today: sort by avg check-in time
      1 — Already checked in today: sort by actual check-in time today (earliest first)
      2 — Already checked out today: sort by actual check-in time today
      3 — On leave
      4 — Supervisors (don't track time)
    """
    LARGE_VAL = 9999

    def sort_key(emp):
        eid = emp.id
        status_info = status_map.get(eid, {})
        status = status_info.get("status", "not_started")
        on_leave = on_leave_map.get(eid, False)
        avg = avg_times.get(eid, {})
        actual_mins = status_info.get("minutes")

        # 1. Supervisors always at the bottom
        if emp.role.value == "supervisor":
            return (4, LARGE_VAL, emp.name.lower())

        # 2. On leave
        if on_leave:
            return (3, LARGE_VAL, emp.name.lower())

        # 3. Time-tracking roles
        if status == "not_started":
            # Tier 0: Not checked in yet. Sort by their typical (average) start time.
            avg_ci = avg.get("avg_checkin")
            return (0, avg_ci if avg_ci is not None else LARGE_VAL, emp.name.lower())
        
        if status == "checked_in":
            # Tier 1: Working. Sort by when they started today.
            return (1, actual_mins if actual_mins is not None else LARGE_VAL, emp.name.lower())
        
        if status == "checked_out":
            # Tier 2: Finished.
            return (2, actual_mins if actual_mins is not None else LARGE_VAL, emp.name.lower())

        return (5, LARGE_VAL, emp.name.lower())

    return sorted(employees, key=sort_key)


@router.get("/innovations", response_class=HTMLResponse)
async def innovations_page(request: Request):
    """Show all innovations."""
    import os
    import json
    json_path = os.path.join("app", "static", "lockheed_weapons.json")
    weapons = []
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                weapons = json.load(f)
        except Exception:
            pass
    
    return templates.TemplateResponse("innovations.html", {
        "request": request,
        "weapons": weapons,
    })


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    """Show employee list for login."""
    # If already logged in, redirect to dashboard
    emp = get_current_employee(request, db)
    if emp:
        return RedirectResponse(url="/", status_code=303)

    employees = db.query(Employee).filter(Employee.is_active == True).order_by(Employee.name).all()
    status_map = _get_employee_status(db, employees)
    avg_times = _get_avg_times(db, employees)
    on_leave_map = {e.id: _is_on_leave_today(db, e.id) for e in employees}
    now_hour = datetime.now().hour

    sorted_employees = _smart_sort_employees(employees, status_map, avg_times, on_leave_map, now_hour)

    return templates.TemplateResponse("login.html", {
        "request": request,
        "employees": sorted_employees,
        "selected_employee": None,
        "error": None,
        "status_map": status_map,
        "on_leave_map": on_leave_map,
        "weapon": _get_random_weapon(),
    })


def _get_random_weapon():
    """Pick a random Lockheed weapon system from the static JSON."""
    import os
    import json
    import random
    json_path = os.path.join("app", "static", "lockheed_weapons.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                weapons = json.load(f)
                if weapons:
                    return random.choice(weapons)
        except Exception:
            pass
    return None


@router.get("/login/{employee_id}", response_class=HTMLResponse)
async def login_pin_page(employee_id: int, request: Request, db: Session = Depends(get_db)):
    """Show PIN entry for a specific employee."""
    emp = get_current_employee(request, db)
    if emp:
        return RedirectResponse(url="/", status_code=303)

    selected = db.query(Employee).filter(Employee.id == employee_id, Employee.is_active == True).first()
    if not selected:
        return RedirectResponse(url="/login", status_code=303)

    employees = db.query(Employee).filter(Employee.is_active == True).order_by(Employee.name).all()
    return templates.TemplateResponse("login.html", {
        "request": request,
        "employees": employees,
        "selected_employee": selected,
        "error": None,
        "weapon": _get_random_weapon(),
    })


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    employee_id: int = Form(...),
    pin: str = Form(...),
    db: Session = Depends(get_db),
):
    """Validate PIN for the selected employee and create session."""
    selected = db.query(Employee).filter(Employee.id == employee_id, Employee.is_active == True).first()
    employees = db.query(Employee).filter(Employee.is_active == True).order_by(Employee.name).all()

    if not selected or not verify_pin(pin, selected.pin_hash):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "employees": employees,
            "selected_employee": selected,
            "error": "Invalid PIN. Please try again.",
            "weapon": _get_random_weapon(),
        })

    matched = selected

    # Determine redirect URL
    redirect_url = "/"
    if matched.pin_needs_reset:
        redirect_url = "/reset-pin"

    # Create session
    token = create_session_token(matched.id)
    response = RedirectResponse(url=redirect_url, status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=8 * 60 * 60,
    )

    log_action(
        db, action="login", entity_type="Employee",
        entity_id=matched.id, employee_id=matched.id,
        ip_address=request.client.host if request.client else "",
    )

    return response


@router.get("/reset-pin", response_class=HTMLResponse)
async def reset_pin_page(request: Request, db: Session = Depends(get_db)):
    """Show PIN reset form."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse("reset_pin.html", {
        "request": request,
        "employee": employee,
        "error": None,
    })


@router.post("/reset-pin", response_class=HTMLResponse)
async def reset_pin_submit(
    request: Request,
    new_pin: str = Form(...),
    confirm_pin: str = Form(...),
    db: Session = Depends(get_db),
):
    """Update employee PIN."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    if new_pin != confirm_pin:
        return templates.TemplateResponse("reset_pin.html", {
            "request": request,
            "employee": employee,
            "error": "PINs do not match.",
        })

    if len(new_pin) < 4:
        return templates.TemplateResponse("reset_pin.html", {
            "request": request,
            "employee": employee,
            "error": "PIN must be at least 4 digits.",
        })

    # Update PIN
    from app.auth import hash_pin
    employee.pin_hash = hash_pin(new_pin)
    employee.pin_needs_reset = False
    db.commit()

    log_action(
        db, action="reset_pin", entity_type="Employee",
        entity_id=employee.id, employee_id=employee.id,
        ip_address=request.client.host if request.client else "",
    )

    return RedirectResponse(url="/", status_code=303)


@router.get("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    """Clear session and redirect to login."""
    emp = get_current_employee(request, db)
    if emp:
        log_action(
            db, action="logout", entity_type="Employee",
            entity_id=emp.id, employee_id=emp.id,
            ip_address=request.client.host if request.client else "",
        )

    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
