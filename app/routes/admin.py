"""Admin routes — user management, remote authorizations."""

from datetime import date, datetime, timedelta, time

from fastapi import APIRouter, Request, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_employee, hash_pin
from app.models import (
    Employee, Role, RemoteAuthorization, AuthorizationStatus,
    DailySummary, TimeEntry, EntryType, LeaveRequest, LeaveStatus,
    OffsiteEntry,
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
    email: str = Form(""),
    db: Session = Depends(get_db),
):
    """Add a new employee."""
    employee = get_current_employee(request, db)
    if not employee or employee.role not in (Role.manager, Role.supervisor):
        return RedirectResponse(url="/login", status_code=303)

    new_emp = Employee(
        name=name,
        email=email.strip() or None,
        pin_hash=hash_pin(pin),
        role=Role(role),
        is_active=True,
    )
    db.add(new_emp)
    db.commit()

    log_action(
        db, action="add_employee", entity_type="Employee",
        entity_id=new_emp.id, employee_id=employee.id,
        new_values={"name": name, "role": role, "email": email.strip() or None},
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
    if not employee or employee.role not in (Role.manager, Role.supervisor):
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


@router.post("/admin/employee/{emp_id}/edit")
async def edit_employee(
    emp_id: int,
    request: Request,
    name: str = Form(...),
    email: str = Form(""),
    role: str = Form("employee"),
    reset_pin: str = Form(""),
    db: Session = Depends(get_db),
):
    """Edit an employee's details."""
    employee = get_current_employee(request, db)
    if not employee or employee.role not in (Role.manager, Role.supervisor):
        return RedirectResponse(url="/login", status_code=303)

    target = db.query(Employee).filter(Employee.id == emp_id).first()
    if not target:
        return RedirectResponse(url="/admin", status_code=303)

    old_values = {"name": target.name, "email": target.email, "role": target.role.value}

    target.name = name.strip()
    target.email = email.strip() or None
    target.role = Role(role)

    # Optionally reset PIN
    if reset_pin.strip():
        target.pin_hash = hash_pin(reset_pin.strip())
        target.pin_needs_reset = True

    db.commit()

    log_action(
        db, action="edit_employee", entity_type="Employee",
        entity_id=target.id, employee_id=employee.id,
        old_values=old_values,
        new_values={"name": target.name, "email": target.email, "role": target.role.value},
        ip_address=request.client.host if request.client else "",
    )

    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/employee/{emp_id}/delete")
async def delete_employee(
    emp_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delete an employee (only if they have no time entries)."""
    employee = get_current_employee(request, db)
    if not employee or employee.role not in (Role.manager, Role.supervisor):
        return RedirectResponse(url="/login", status_code=303)

    target = db.query(Employee).filter(Employee.id == emp_id).first()
    if not target or target.id == employee.id:
        return RedirectResponse(url="/admin", status_code=303)

    # Check for existing time data
    has_entries = db.query(TimeEntry).filter(TimeEntry.employee_id == emp_id).first()
    if has_entries:
        # Can't delete — has data. Deactivate instead.
        target.is_active = False
        db.commit()
        return RedirectResponse(url="/admin", status_code=303)

    log_action(
        db, action="delete_employee", entity_type="Employee",
        entity_id=target.id, employee_id=employee.id,
        old_values={"name": target.name, "role": target.role.value},
        ip_address=request.client.host if request.client else "",
    )

    db.delete(target)
    db.commit()

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

    # Pre-fetch all entries for these employees for the week
    all_emp_ids = [e.id for e in employees]
    all_week_entries = db.query(TimeEntry).filter(
        TimeEntry.employee_id.in_(all_emp_ids),
        TimeEntry.date >= week_start,
        TimeEntry.date <= week_end
    ).all()
    
    # Organize entries by [employee_id][date]
    entry_lookup = {}
    for e in all_week_entries:
        if e.employee_id not in entry_lookup:
            entry_lookup[e.employee_id] = {}
        if e.date not in entry_lookup[e.employee_id]:
            entry_lookup[e.employee_id][e.date] = []
        entry_lookup[e.employee_id][e.date].append(e)

    # Build grid: employee rows × day columns
    rows = []
    for emp in employees:
        summaries = db.query(DailySummary).filter(
            DailySummary.employee_id == emp.id,
            DailySummary.date >= week_start,
            DailySummary.date <= week_end,
        ).all()
        summary_map = {s.date: s for s in summaries}

        # Check-in status for "Live" view if week is current
        today_entry = None
        if is_current_week or week_start <= today <= week_end:
            today_entry = db.query(TimeEntry).filter(
                TimeEntry.employee_id == emp.id,
                TimeEntry.date == today
            ).order_by(TimeEntry.declared_time.desc()).first()
            
            # Get all entries for today to calculate live hours
            today_time_entries = db.query(TimeEntry).filter(
                TimeEntry.employee_id == emp.id,
                TimeEntry.date == today
            ).all()
            today_offsite_entries = db.query(OffsiteEntry).filter(
                OffsiteEntry.employee_id == emp.id,
                OffsiteEntry.date == today
            ).all()

        # Check for approved or pending leave days
        leaves = db.query(LeaveRequest).filter(
            LeaveRequest.employee_id == emp.id,
            LeaveRequest.status.in_([LeaveStatus.approved, LeaveStatus.pending]),
            LeaveRequest.start_date <= week_end,
            LeaveRequest.end_date >= week_start,
        ).all()
        
        leave_map = {} # date -> LeaveRequest
        for l in leaves:
            d_curr = max(l.start_date, week_start)
            while d_curr <= min(l.end_date, week_end):
                # Approved takes priority in the map
                if d_curr not in leave_map or l.status == LeaveStatus.approved:
                    leave_map[d_curr] = l
                d_curr += timedelta(days=1)

        day_cells = []
        week_total = 0.0
        week_target = 0.0
        for day_info in days:
            d = day_info["date"]
            s = summary_map.get(d)
            target = day_info["target"]
            l_req = leave_map.get(d)
            is_leave_approved = (l_req and l_req.status == LeaveStatus.approved)
            is_leave_pending = (l_req and l_req.status == LeaveStatus.pending)

            worked = s.total_hours if s else 0.0
            
            # Live tracking for today
            is_in = False
            live_worked = worked
            live_base_hours = worked
            last_checkin_iso = None
            if d == today:
                is_in = (today_entry and today_entry.entry_type == EntryType.check_in)
                if is_in:
                    # Calculate live elapsed time
                    from app.services.time_calc import calculate_daily_hours
                    # Hours from COMPLETED sessions and offsite entries
                    live_base_hours = calculate_daily_hours(today_time_entries, today_offsite_entries)
                    
                    # Add current session for server-side display fallback
                    now = datetime.now()
                    session_delta = (now - today_entry.declared_time).total_seconds() / 3600.0
                    live_worked = round(live_base_hours + max(0, session_delta), 2)
                    last_checkin_iso = today_entry.declared_time.isoformat()
                else:
                    live_base_hours = worked
                    last_checkin_iso = None
            
            leave_hrs = s.leave_hours if s else 0.0
            
            # For full-day leave requests, we treat the whole day as target hours for display/compliance
            if (is_leave_approved or is_leave_pending) and leave_hrs <= 0.0:
                leave_hrs = target
                
            leave_approved_flag = s.leave_approved if s else is_leave_approved
            leave_type_val = s.leave_type.value if s and s.leave_type else (l_req.leave_type.value if l_req else None)
            
            compliant = s.is_compliant if s else ((is_leave_approved or is_in) and (live_worked + leave_hrs) >= target)
            lunch_pending = (s.lunch_end_of_day and not s.lunch_approved) if s else False
            lunch_approved_flag = (s.lunch_end_of_day and s.lunch_approved) if s else False
            
            pto_pending = (leave_hrs > 0 and not leave_approved_flag) or is_leave_pending
            pto_approved = (leave_hrs > 0 and leave_approved_flag) or is_leave_approved

            # Effective total: only include approved PTO
            approved_leave = leave_hrs if leave_approved_flag else 0.0
            effective = live_worked + approved_leave
            week_total = round(week_total + effective, 2)
            if not is_leave_approved:
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
            elif is_leave_approved:
                status = "leave"
            elif d > today:
                status = "future"
            elif worked <= 0 and leave_hrs <= 0 and d < today:
                status = "missing"
            elif compliant:
                status = "compliant"
            else:
                status = "partial"

            # Calculate punctuality dots
            day_entries = entry_lookup.get(emp.id, {}).get(d, [])
            check_ins = [e for e in day_entries if e.entry_type == EntryType.check_in]
            check_outs = [e for e in day_entries if e.entry_type == EntryType.check_out]

            ci_dot = "empty"
            if check_ins:
                first_ci = min(e.declared_time for e in check_ins)
                ci_dot = "green" if first_ci.time() <= time(8, 0) else "red"
            
            co_dot = "empty"
            if check_outs:
                last_co = max(e.declared_time for e in check_outs)
                co_dot = "green" if last_co.time() <= time(17, 0) else "red"

            day_cells.append({
                "date": d,
                "worked": worked,
                "is_in": is_in,
                "live_worked": live_worked,
                "live_base_hours": live_base_hours if is_in else None,
                "last_checkin_iso": last_checkin_iso,
                "remaining": round(max(0, target - live_worked - approved_leave), 2),
                "ci_dot": ci_dot,
                "co_dot": co_dot,
                "leave_hours": leave_hrs,
                "display_leave_hours": leave_hrs if pto_pending or pto_approved else 0.0,
                "leave_type_label": leave_type_label,
                "pto_pending": pto_pending,
                "pto_approved": pto_approved,
                "effective": round(effective, 2),
                "target": target,
                "status": status,
                "is_leave": is_leave_approved,
                "lunch_pending": lunch_pending,
                "lunch_approved": lunch_approved_flag,
                "summary_id": s.id if s else None,
                "leave_request_id": l_req.id if is_leave_pending else None,
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

    # Capture stats for audit before update
    old_total = summary.total_hours

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
    
    # Notify managers of administrative modification
    import threading
    from app.services.email import send_past_day_modification_email
    managers = db.query(Employee).filter(Employee.role == Role.manager).all()
    mgr_emails = [m.email for m in managers if m.email]
    
    target_employee = db.query(Employee).filter(Employee.id == summary.employee_id).first()
    emp_name = target_employee.name if target_employee else "Unknown Employee"
    
    threading.Thread(
        target=send_past_day_modification_email,
        args=(
            employee.name, emp_name, str(summary.date), 
            "Approve Lunch EOD Deviation", "Approved via timesheet", 
            mgr_emails,
            [
                ("Status", "Pending → Approved"),
                ("Total Worked", f"{old_total}h → {old_total + 1.0}h")
            ]
        ),
        daemon=True,
    ).start()

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

    # Capture stats for audit
    old_approved = summary.leave_approved
    hrs = summary.leave_hours
    ltype = summary.leave_type.value if summary.leave_type else "PTO"

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

    # Notify managers of administrative modification
    import threading
    from app.services.email import send_past_day_modification_email
    managers = db.query(Employee).filter(Employee.role == Role.manager).all()
    mgr_emails = [m.email for m in managers if m.email]
    
    target_employee = db.query(Employee).filter(Employee.id == summary.employee_id).first()
    emp_name = target_employee.name if target_employee else "Unknown Employee"
    
    threading.Thread(
        target=send_past_day_modification_email,
        args=(
            employee.name, emp_name, str(summary.date), 
            "Approve PTO Request", "Approved via timesheet", 
            mgr_emails,
            [
                ("Adjustment", f"{hrs}h {ltype.capitalize()}"),
                ("Approval Status", "Pending → Approved")
            ]
        ),
        daemon=True,
    ).start()

    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/admin/approve-leave-request")
async def admin_approve_leave_request(
    request: Request,
    leave_id: int = Form(...),
    week: str = Form(None),
    db: Session = Depends(get_db),
):
    """Approve a full-day leave request from the timesheet."""
    employee = get_current_employee(request, db)
    if not employee or employee.role not in (Role.manager, Role.supervisor):
        return RedirectResponse(url="/login", status_code=303)

    leave = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).first()
    if leave and leave.status == LeaveStatus.pending:
        old_status = leave.status.value
        leave.status = LeaveStatus.approved
        leave.approved_by = employee.id
        db.commit()

        log_action(
            db, action="approve_leave_admin", entity_type="LeaveRequest",
            entity_id=leave.id, employee_id=employee.id,
            old_values={"status": old_status},
            new_values={"status": "approved"},
            ip_address=request.client.host if request.client else "",
        )

    redirect_url = f"/admin/timesheet?week={week}" if week else "/admin/timesheet"
    return RedirectResponse(url=redirect_url, status_code=303)


# ── Feature Configuration ─────────────────────────────────────────────

@router.get("/admin/config", response_class=HTMLResponse)
async def config_page(request: Request, db: Session = Depends(get_db)):
    """Feature configuration page."""
    employee = get_current_employee(request, db)
    if not employee or employee.role != Role.manager:
        return RedirectResponse(url="/login", status_code=303)

    from app.services.settings import get_all_settings
    settings = get_all_settings(db)

    return templates.TemplateResponse("admin_config.html", {
        "request": request,
        "employee": employee,
        "settings": settings,
        "success": request.query_params.get("saved"),
    })


@router.post("/admin/config", response_class=HTMLResponse)
async def save_config(request: Request, db: Session = Depends(get_db)):
    """Save feature configuration toggles."""
    employee = get_current_employee(request, db)
    if not employee or employee.role != Role.manager:
        return RedirectResponse(url="/login", status_code=303)

    from app.services.settings import set_setting, FEATURE_DEFAULTS

    form = await request.form()

    for key, info in FEATURE_DEFAULTS.items():
        # Check if the existing value is a "boolean" string
        if info["value"] in ("true", "false"):
            # Checkbox: present in form → true, absent → false
            value = "true" if form.get(key) else "false"
        else:
            # Regular text/number input
            value = form.get(key, info["value"])
        set_setting(db, key, value)

    log_action(
        db, action="update_config", entity_type="AppSetting",
        entity_id=None, employee_id=employee.id,
        new_values={k: form.get(k, "off") for k in FEATURE_DEFAULTS},
        ip_address=request.client.host if request.client else "",
    )

    return RedirectResponse(url="/admin/config?saved=1", status_code=303)

