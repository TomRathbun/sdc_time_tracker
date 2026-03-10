"""Time entry routes — check-in, check-out, offsite logging."""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_employee
from app.models import (
    TimeEntry, OffsiteEntry, EntryType, LocationType,
    RemoteAuthorization, AuthorizationStatus, Employee, Role
)
from app.services.time_calc import update_daily_summary, get_target_hours
from app.services.audit import log_action

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/time/checkin", response_class=HTMLResponse)
async def checkin_page(request: Request, db: Session = Depends(get_db)):
    """Show check-in form."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    now = datetime.now()
    # Get policy threshold
    from app.services.settings import get_setting
    try:
        threshold = int(get_setting(db, "comment_threshold_minutes"))
    except (ValueError, TypeError):
        threshold = 30

    return templates.TemplateResponse("time_entry.html", {
        "request": request,
        "employee": employee,
        "entry_type": "check_in",
        "now": now,
        "today": date.today(),
        "error": None,
        "comment_threshold": threshold,
    })


@router.post("/time/checkin", response_class=HTMLResponse)
async def checkin_submit(
    request: Request,
    declared_hour: int = Form(...),
    declared_minute: int = Form(...),
    location_type: str = Form("office"),
    comments: str = Form(""),
    db: Session = Depends(get_db),
):
    """Submit a check-in entry."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    today = date.today()
    now = datetime.now()

    # Build declared time
    declared_time = datetime(today.year, today.month, today.day, declared_hour, declared_minute)

    # Prevent check-in from being earlier than the last check-out
    last_checkout = db.query(TimeEntry).filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.date == today,
        TimeEntry.entry_type == EntryType.check_out,
    ).order_by(TimeEntry.declared_time.desc()).first()

    if last_checkout and declared_time < last_checkout.declared_time:
        declared_time = last_checkout.declared_time

    # Determine location type
    loc_type = LocationType(location_type) if location_type in [e.value for e in LocationType] else LocationType.office
    is_remote = loc_type in (LocationType.remote, LocationType.offsite)

    # If remote, check authorization
    authorization_id = None
    if is_remote:
        auth = db.query(RemoteAuthorization).filter(
            RemoteAuthorization.employee_id == employee.id,
            RemoteAuthorization.date == today,
            RemoteAuthorization.status == AuthorizationStatus.active,
        ).first()
        if not auth:
            return templates.TemplateResponse("time_entry.html", {
                "request": request,
                "employee": employee,
                "entry_type": "check_in",
                "now": now,
                "today": today,
                "error": "No active remote work authorization found for today. Please contact your Engineering Manager.",
            })
        authorization_id = auth.id

    # ENFORCE COMMENT POLICY
    from app.services.settings import get_setting
    try:
        threshold = int(get_setting(db, "comment_threshold_minutes"))
    except (ValueError, TypeError):
        threshold = 30
    
    diff_minutes = abs((now - declared_time).total_seconds()) / 60
    if diff_minutes > threshold and not comments.strip():
        return templates.TemplateResponse("time_entry.html", {
            "request": request,
            "employee": employee,
            "entry_type": "check_in",
            "now": now,
            "today": today,
            "error": f"Policy Violation: You must provide a comment when your set time ({declared_time.strftime('%H:%M')}) differs from actual time ({now.strftime('%H:%M')}) by more than {threshold} minutes.",
        })

    entry = TimeEntry(
        employee_id=employee.id,
        date=today,
        declared_time=declared_time,
        submission_time=now,
        entry_type=EntryType.check_in,
        location_type=loc_type,
        is_remote=is_remote,
        authorization_id=authorization_id,
        comments=comments,
    )
    db.add(entry)
    db.commit()

    # Update daily summary
    update_daily_summary(db, employee.id, today)

    # Audit log
    log_action(
        db, action="check_in", entity_type="TimeEntry",
        entity_id=entry.id, employee_id=employee.id,
        new_values={
            "declared_time": str(declared_time),
            "submission_time": str(now),
            "location_type": loc_type.value,
        },
        ip_address=request.client.host if request.client else "",
    )

    # TRIGGER POLICY ALERT EMAIL IF THRESHOLD EXCEEDED
    from app.services.settings import get_setting
    if diff_minutes > threshold and get_setting(db, "manager_policy_alert_enabled") == "true":
        import threading
        from app.services.email import send_policy_violation_email
        managers = db.query(Employee).filter(Employee.role == Role.manager).all()
        mgr_emails = [m.email for m in managers if m.email]
        threading.Thread(
            target=send_policy_violation_email,
            args=(employee.name, employee.email, mgr_emails, "check_in", declared_time, now, threshold, comments),
            daemon=True,
        ).start()

    # Send check-in confirmation email (non-blocking, if enabled)
    from app.services.settings import get_bool_setting
    if employee.email and get_bool_setting(db, "checkin_email_enabled"):
        import threading
        from app.services.email import send_checkin_email
        from app.models import LeaveRequest, LeaveStatus

        target = get_target_hours(today)
        expected_checkout = declared_time + timedelta(hours=target)

        # Gather upcoming leave in next 2 weeks
        cutoff = today + timedelta(days=14)
        leaves = db.query(LeaveRequest).filter(
            LeaveRequest.employee_id == employee.id,
            LeaveRequest.start_date <= cutoff,
            LeaveRequest.end_date >= today,
            LeaveRequest.status == LeaveStatus.approved,
        ).all()
        upcoming_leave = [
            {
                "start_date": lv.start_date.strftime("%b %d"),
                "end_date": lv.end_date.strftime("%b %d"),
                "leave_type": lv.leave_type.value,
            }
            for lv in leaves
        ]

        threading.Thread(
            target=send_checkin_email,
            args=(employee, declared_time, expected_checkout, upcoming_leave),
            daemon=True,
        ).start()

    # Detect gap: was there a previous checkout today?
    # If so, offer to log the gap as remote site work
    prev_checkout = db.query(TimeEntry).filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.date == today,
        TimeEntry.entry_type == EntryType.check_out,
    ).order_by(TimeEntry.declared_time.desc()).first()

    if prev_checkout:
        gap_start = prev_checkout.declared_time
        gap_end = declared_time
        gap_minutes = (gap_end - gap_start).total_seconds() / 60
        # Only prompt if gap is at least 15 minutes
        if gap_minutes >= 15:
            return RedirectResponse(
                url=f"/time/offsite-gap?start={gap_start.strftime('%H:%M')}&end={gap_end.strftime('%H:%M')}",
                status_code=303,
            )

    return RedirectResponse(url="/", status_code=303)


@router.get("/time/checkout", response_class=HTMLResponse)
async def checkout_page(request: Request, db: Session = Depends(get_db)):
    """Show check-out form."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    now = datetime.now()
    # Get policy threshold
    from app.services.settings import get_setting
    try:
        threshold = int(get_setting(db, "comment_threshold_minutes"))
    except (ValueError, TypeError):
        threshold = 30

    return templates.TemplateResponse("time_entry.html", {
        "request": request,
        "employee": employee,
        "entry_type": "check_out",
        "now": now,
        "today": date.today(),
        "error": None,
        "comment_threshold": threshold,
    })


@router.post("/time/checkout", response_class=HTMLResponse)
async def checkout_submit(
    request: Request,
    declared_hour: int = Form(...),
    declared_minute: int = Form(...),
    location_type: str = Form("office"),
    lunch_end_of_day: bool = Form(False),
    comments: str = Form(""),
    db: Session = Depends(get_db),
):
    """Submit a check-out entry."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    today = date.today()
    now = datetime.now()
    target = get_target_hours(today)

    declared_time = datetime(today.year, today.month, today.day, declared_hour, declared_minute)

    loc_type = LocationType(location_type) if location_type in [e.value for e in LocationType] else LocationType.office
    is_remote = loc_type in (LocationType.remote, LocationType.offsite)

    # ENFORCE COMMENT POLICY
    from app.services.settings import get_setting
    try:
        threshold = int(get_setting(db, "comment_threshold_minutes"))
    except (ValueError, TypeError):
        threshold = 30
    
    diff_minutes = abs((now - declared_time).total_seconds()) / 60
    if diff_minutes > threshold and not comments.strip():
        return templates.TemplateResponse("time_entry.html", {
            "request": request,
            "employee": employee,
            "entry_type": "check_out",
            "now": now,
            "today": today,
            "error": f"Policy Violation: You must provide a comment when your set time ({declared_time.strftime('%H:%M')}) differs from actual time ({now.strftime('%H:%M')}) by more than {threshold} minutes.",
        })

    entry = TimeEntry(
        employee_id=employee.id,
        date=today,
        declared_time=declared_time,
        submission_time=now,
        entry_type=EntryType.check_out,
        location_type=loc_type,
        is_remote=is_remote,
        comments=comments,
    )
    db.add(entry)
    db.commit()

    # Update daily summary with lunch info
    update_daily_summary(db, employee.id, today, lunch_end_of_day=lunch_end_of_day)

    log_action(
        db, action="check_out", entity_type="TimeEntry",
        entity_id=entry.id, employee_id=employee.id,
        new_values={
            "declared_time": str(declared_time),
            "submission_time": str(now),
            "location_type": loc_type.value,
            "lunch_end_of_day": lunch_end_of_day,
        },
        ip_address=request.client.host if request.client else "",
    )

    # TRIGGER POLICY ALERT EMAIL IF THRESHOLD EXCEEDED
    from app.services.settings import get_setting
    if diff_minutes > threshold and get_setting(db, "manager_policy_alert_enabled") == "true":
        import threading
        from app.services.email import send_policy_violation_email
        managers = db.query(Employee).filter(Employee.role == Role.manager).all()
        mgr_emails = [m.email for m in managers if m.email]
        threading.Thread(
            target=send_policy_violation_email,
            args=(employee.name, employee.email, mgr_emails, "check_out", declared_time, now, threshold, comments),
            daemon=True,
        ).start()

    return RedirectResponse(url="/", status_code=303)


@router.get("/time/offsite", response_class=HTMLResponse)
async def offsite_page(request: Request, db: Session = Depends(get_db)):
    """Show offsite work logging form."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    now = datetime.now()
    return templates.TemplateResponse("offsite.html", {
        "request": request,
        "employee": employee,
        "now": now,
        "today": date.today(),
        "error": None,
    })


@router.post("/time/offsite", response_class=HTMLResponse)
async def offsite_submit(
    request: Request,
    location: str = Form(...),
    start_hour: int = Form(...),
    start_minute: int = Form(...),
    end_hour: int = Form(...),
    end_minute: int = Form(...),
    comments: str = Form(""),
    db: Session = Depends(get_db),
):
    """Submit an offsite work entry."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    today = date.today()
    now = datetime.now()

    start_time = datetime(today.year, today.month, today.day, start_hour, start_minute)
    end_time = datetime(today.year, today.month, today.day, end_hour, end_minute)

    if end_time <= start_time:
        return templates.TemplateResponse("offsite.html", {
            "request": request,
            "employee": employee,
            "now": now,
            "today": today,
            "error": "End time must be after start time.",
        })

    entry = OffsiteEntry(
        employee_id=employee.id,
        date=today,
        location=location,
        start_time=start_time,
        end_time=end_time,
        comments=comments,
        submission_time=now,
        needs_review=True,
    )
    db.add(entry)
    db.commit()

    update_daily_summary(db, employee.id, today)

    log_action(
        db, action="offsite_log", entity_type="OffsiteEntry",
        entity_id=entry.id, employee_id=employee.id,
        new_values={
            "location": location,
            "start_time": str(start_time),
            "end_time": str(end_time),
        },
        ip_address=request.client.host if request.client else "",
    )

    return RedirectResponse(url="/", status_code=303)


@router.get("/time/offsite-gap", response_class=HTMLResponse)
async def offsite_gap_page(
    request: Request,
    start: str = "",
    end: str = "",
    db: Session = Depends(get_db),
):
    """Show prompt for remote site work detected between checkout and check-in."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    today = date.today()

    # Parse times
    try:
        start_parts = start.split(":")
        start_hour, start_min = int(start_parts[0]), int(start_parts[1])
    except (ValueError, IndexError):
        start_hour, start_min = 8, 0

    try:
        end_parts = end.split(":")
        end_hour, end_min = int(end_parts[0]), int(end_parts[1])
    except (ValueError, IndexError):
        end_hour, end_min = 17, 0

    # Calculate gap duration
    gap_minutes = (end_hour * 60 + end_min) - (start_hour * 60 + start_min)
    gap_hours = round(gap_minutes / 60, 1)

    return templates.TemplateResponse("offsite_gap.html", {
        "request": request,
        "employee": employee,
        "today": today,
        "start_hour": start_hour,
        "start_min": start_min,
        "end_hour": end_hour,
        "end_min": end_min,
        "gap_hours": gap_hours,
        "start_display": f"{start_hour:02d}:{start_min:02d}",
        "end_display": f"{end_hour:02d}:{end_min:02d}",
        "error": None,
    })


@router.post("/time/offsite-gap", response_class=HTMLResponse)
async def offsite_gap_submit(
    request: Request,
    location: str = Form(...),
    start_hour: int = Form(...),
    start_minute: int = Form(...),
    end_hour: int = Form(...),
    end_minute: int = Form(...),
    comments: str = Form(""),
    db: Session = Depends(get_db),
):
    """Confirm remote site work for the gap period."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    today = date.today()
    now = datetime.now()

    start_time = datetime(today.year, today.month, today.day, start_hour, start_minute)
    end_time = datetime(today.year, today.month, today.day, end_hour, end_minute)

    if end_time <= start_time:
        return RedirectResponse(url="/", status_code=303)

    entry = OffsiteEntry(
        employee_id=employee.id,
        date=today,
        location=location.strip() or "Remote site",
        start_time=start_time,
        end_time=end_time,
        comments=comments,
        submission_time=now,
        needs_review=True,
    )
    db.add(entry)
    db.commit()

    update_daily_summary(db, employee.id, today)

    log_action(
        db, action="offsite_gap", entity_type="OffsiteEntry",
        entity_id=entry.id, employee_id=employee.id,
        new_values={
            "location": entry.location,
            "start_time": str(start_time),
            "end_time": str(end_time),
            "auto_detected": True,
        },
        ip_address=request.client.host if request.client else "",
    )

    return RedirectResponse(url="/", status_code=303)


@router.get("/time/past-day", response_class=HTMLResponse)
async def past_day_page(
    request: Request,
    target_date: str = None,
    db: Session = Depends(get_db),
):
    """Show form to enter a full timeline for a past day."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    today = date.today()

    if target_date:
        try:
            selected_date = date.fromisoformat(target_date)
        except ValueError:
            selected_date = today - timedelta(days=1)
    else:
        selected_date = today - timedelta(days=1)

    target = get_target_hours(selected_date)

    # Get existing entries for the selected date
    from app.models import DailySummary
    existing_checkins = db.query(TimeEntry).filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.date == selected_date,
    ).order_by(TimeEntry.declared_time).all()

    existing_offsite = db.query(OffsiteEntry).filter(
        OffsiteEntry.employee_id == employee.id,
        OffsiteEntry.date == selected_date,
    ).order_by(OffsiteEntry.start_time).all()

    summary = db.query(DailySummary).filter(
        DailySummary.employee_id == employee.id,
        DailySummary.date == selected_date,
    ).first()

    # Build recent workdays for date picker
    recent_days = []
    for i in range(1, 15):  # past 2 weeks, skip today
        d = today - timedelta(days=i)
        t = get_target_hours(d)
        if t > 0:
            recent_days.append({"date": d, "label": d.strftime("%a %b %d"), "target": t})

    return templates.TemplateResponse("past_day.html", {
        "request": request,
        "employee": employee,
        "today": today,
        "selected_date": selected_date,
        "target": target,
        "existing_entries": existing_checkins,
        "existing_offsite": existing_offsite,
        "current_worked": summary.total_hours if summary else 0.0,
        "recent_days": recent_days,
        "error": None,
        "success": None,
    })


@router.post("/time/past-day", response_class=HTMLResponse)
async def past_day_submit(
    request: Request,
    entry_date: str = Form(...),
    checkin_hour: int = Form(...),
    checkin_minute: int = Form(...),
    checkout_hour: int = Form(...),
    checkout_minute: int = Form(...),
    lunch_end_of_day: bool = Form(False),
    offsite_location: str = Form(""),
    offsite_start_hour: int = Form(-1),
    offsite_start_minute: int = Form(0),
    offsite_end_hour: int = Form(-1),
    offsite_end_minute: int = Form(0),
    comments: str = Form(""),
    db: Session = Depends(get_db),
):
    """Submit a full day's timeline for a past day."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    now = datetime.now()

    try:
        selected_date = date.fromisoformat(entry_date)
    except ValueError:
        return RedirectResponse(url="/time/past-day", status_code=303)

    # Comments are mandatory for past day entries
    if not comments or not comments.strip():
        return RedirectResponse(
            url=f"/time/past-day?target_date={entry_date}",
            status_code=303,
        )

    # Build datetimes
    checkin_time = datetime(
        selected_date.year, selected_date.month, selected_date.day,
        checkin_hour, checkin_minute,
    )
    checkout_time = datetime(
        selected_date.year, selected_date.month, selected_date.day,
        checkout_hour, checkout_minute,
    )

    if checkout_time <= checkin_time:
        return RedirectResponse(
            url=f"/time/past-day?target_date={entry_date}",
            status_code=303,
        )

    # CAPTURE OLD STATE BEFORE DELETING
    old_entries = db.query(TimeEntry).filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.date == selected_date
    ).order_by(TimeEntry.declared_time).all()
    
    old_ci = "None"
    old_co = "None"
    if old_entries:
        cis = [e.declared_time.strftime("%H:%M") for e in old_entries if e.entry_type == EntryType.check_in]
        cos = [e.declared_time.strftime("%H:%M") for e in old_entries if e.entry_type == EntryType.check_out]
        old_ci = ", ".join(cis) if cis else "None"
        old_co = ", ".join(cos) if cos else "None"

    # DELETE EXISTING ENTRIES FOR THIS DAY (Clean slate for past day entry)
    db.query(TimeEntry).filter(TimeEntry.employee_id == employee.id, TimeEntry.date == selected_date).delete()
    db.query(OffsiteEntry).filter(OffsiteEntry.employee_id == employee.id, OffsiteEntry.date == selected_date).delete()
    db.flush()

    # Create check-in entry
    ci = TimeEntry(
        employee_id=employee.id,
        date=selected_date,
        declared_time=checkin_time,
        submission_time=now,
        entry_type=EntryType.check_in,
        location_type=LocationType.office,
        is_remote=False,
        comments=comments or f"Past day entry ({selected_date})",
    )
    db.add(ci)

    # Create check-out entry
    co = TimeEntry(
        employee_id=employee.id,
        date=selected_date,
        declared_time=checkout_time,
        submission_time=now,
        entry_type=EntryType.check_out,
        location_type=LocationType.office,
        is_remote=False,
        comments=comments or f"Past day entry ({selected_date})",
    )
    db.add(co)

    # Optional offsite entry
    if offsite_location.strip() and offsite_start_hour >= 0 and offsite_end_hour >= 0:
        offsite_start = datetime(
            selected_date.year, selected_date.month, selected_date.day,
            offsite_start_hour, offsite_start_minute,
        )
        offsite_end = datetime(
            selected_date.year, selected_date.month, selected_date.day,
            offsite_end_hour, offsite_end_minute,
        )
        if offsite_end > offsite_start:
            oe = OffsiteEntry(
                employee_id=employee.id,
                date=selected_date,
                location=offsite_location.strip(),
                start_time=offsite_start,
                end_time=offsite_end,
                comments=comments,
                submission_time=now,
                needs_review=True,
            )
            db.add(oe)

    db.commit()

    # Update daily summary (lunch needs separate call with flag)
    update_daily_summary(
        db, employee.id, selected_date,
        lunch_end_of_day=lunch_end_of_day,
    )

    log_action(
        db, action="past_day_entry", entity_type="TimeEntry",
        entity_id=employee.id, employee_id=employee.id,
        new_values={
            "date": str(selected_date),
            "checkin": str(checkin_time),
            "checkout": str(checkout_time),
            "lunch_end_of_day": lunch_end_of_day,
            "offsite_location": offsite_location.strip() or None,
        },
        ip_address=request.client.host if request.client else "",
    )

    # Notify managers of past day modification
    import threading
    from app.services.email import send_past_day_modification_email
    managers = db.query(Employee).filter(Employee.role == Role.manager).all()
    mgr_emails = [m.email for m in managers if m.email]
    
    threading.Thread(
        target=send_past_day_modification_email,
        args=(
            employee.name, 
            employee.name, 
            str(selected_date), 
            "Manual Past Day Entry", 
            comments, 
            mgr_emails,
            [
                ("Check-In Time", f"{old_ci} → {checkin_hour:02d}:{checkin_minute:02d}"),
                ("Check-Out Time", f"{old_co} → {checkout_hour:02d}:{checkout_minute:02d}"),
                ("Lunch EOD", "Enabled" if lunch_end_of_day else "Disabled"),
                ("Offsite", offsite_location.strip() or "None")
            ]
        ),
        daemon=True,
    ).start()

    return RedirectResponse(url="/", status_code=303)



@router.get("/time/partial-leave", response_class=HTMLResponse)
async def partial_leave_page(
    request: Request,
    target_date: str = None,
    db: Session = Depends(get_db),
):
    """Show PTO form — supports today or past dates for adjustment."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    today = date.today()

    # Parse target date or default to today
    if target_date:
        try:
            selected_date = date.fromisoformat(target_date)
        except ValueError:
            selected_date = today
    else:
        selected_date = today

    target = get_target_hours(selected_date)

    # Get current daily summary for that date
    from app.models import DailySummary
    summary = db.query(DailySummary).filter(
        DailySummary.employee_id == employee.id,
        DailySummary.date == selected_date,
    ).first()

    # Build list of recent workdays for the date picker
    recent_days = []
    for i in range(14):  # past 2 weeks
        d = today - timedelta(days=i)
        t = get_target_hours(d)
        if t > 0:  # only workdays
            recent_days.append({"date": d, "label": d.strftime("%a %b %d"), "target": t})

    return templates.TemplateResponse("partial_leave.html", {
        "request": request,
        "employee": employee,
        "today": today,
        "selected_date": selected_date,
        "target": target,
        "current_leave_hours": summary.leave_hours if summary else 0.0,
        "current_leave_type": summary.leave_type.value if summary and summary.leave_type else None,
        "current_leave_approved": summary.leave_approved if summary else False,
        "current_worked": summary.total_hours if summary else 0.0,
        "recent_days": recent_days,
        "error": None,
    })


@router.post("/time/partial-leave", response_class=HTMLResponse)
async def partial_leave_submit(
    request: Request,
    leave_date: str = Form(...),
    leave_type: str = Form(...),
    leave_hours: float = Form(...),
    db: Session = Depends(get_db),
):
    """Submit or update PTO hours for a given date."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    today = date.today()

    try:
        selected_date = date.fromisoformat(leave_date)
    except ValueError:
        selected_date = today

    target = get_target_hours(selected_date)

    from app.models import DailySummary
    summary = db.query(DailySummary).filter(
        DailySummary.employee_id == employee.id,
        DailySummary.date == selected_date,
    ).first()

    # Validate hours (0 is allowed — it clears PTO)
    if leave_hours < 0 or leave_hours > target:
        recent_days = []
        for i in range(14):
            d = today - timedelta(days=i)
            t = get_target_hours(d)
            if t > 0:
                recent_days.append({"date": d, "label": d.strftime("%a %b %d"), "target": t})
        return templates.TemplateResponse("partial_leave.html", {
            "request": request,
            "employee": employee,
            "today": today,
            "selected_date": selected_date,
            "target": target,
            "current_leave_hours": summary.leave_hours if summary else 0.0,
            "current_leave_type": summary.leave_type.value if summary and summary.leave_type else None,
            "current_leave_approved": summary.leave_approved if summary else False,
            "current_worked": summary.total_hours if summary else 0.0,
            "recent_days": recent_days,
            "error": f"PTO hours must be between 0 and {target}.",
        })

    # Validate leave_type
    if leave_type not in ("vacation", "sick"):
        leave_type = "vacation"

    # Update daily summary with PTO
    old_hrs = summary.leave_hours if summary else 0.0
    old_type = summary.leave_type.value if summary and summary.leave_type else "None"

    update_daily_summary(
        db, employee.id, selected_date,
        leave_hours=leave_hours,
        leave_type=leave_type if leave_hours > 0 else None,
    )

    log_action(
        db, action="pto_request", entity_type="DailySummary",
        entity_id=employee.id, employee_id=employee.id,
        new_values={
            "leave_hours": leave_hours,
            "leave_type": leave_type,
            "date": str(selected_date),
        },
        ip_address=request.client.host if request.client else "",
    )

    # Notify managers of past day PTO adjustment
    if selected_date < date.today():
        import threading
        from app.services.email import send_past_day_modification_email
        managers = db.query(Employee).filter(Employee.role == Role.manager).all()
        mgr_emails = [m.email for m in managers if m.email]
        
        threading.Thread(
            target=send_past_day_modification_email,
            args=(
                employee.name, 
                employee.name, 
                str(selected_date), 
                f"PTO Adjustment", 
                "Manual adjustment via PTO form", 
                mgr_emails,
                [
                    ("Leave Type", f"{old_type.capitalize()} → {leave_type.capitalize()}"),
                    ("Hours Added", f"{old_hrs}h → {leave_hours}h")
                ]
            ),
            daemon=True,
        ).start()

    return RedirectResponse(url="/", status_code=303)
