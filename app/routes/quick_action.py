"""Quick check-in / check-out routes — PIN-verified, no session required."""

import math
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import verify_pin
from app.models import Employee, TimeEntry, EntryType, LocationType
from app.services.time_calc import update_daily_summary
from app.services.audit import log_action

router = APIRouter(prefix="/api")


def round_down_5(dt: datetime) -> datetime:
    """Round a datetime DOWN to the nearest 5-minute mark.
    e.g. 06:23 → 06:20, 06:25 → 06:25
    """
    new_minute = (dt.minute // 5) * 5
    return dt.replace(minute=new_minute, second=0, microsecond=0)


def round_up_5(dt: datetime) -> datetime:
    """Round a datetime UP to the nearest 5-minute mark.
    e.g. 15:16 → 15:20, 15:20 → 15:20
    """
    if dt.minute % 5 == 0 and dt.second == 0:
        return dt.replace(second=0, microsecond=0)
    new_minute = math.ceil(dt.minute / 5) * 5
    if new_minute >= 60:
        # Roll over to next hour
        return (dt.replace(minute=0, second=0, microsecond=0)
                + timedelta(hours=1))
    return dt.replace(minute=new_minute, second=0, microsecond=0)


@router.post("/quick-checkin")
async def quick_checkin(
    request: Request,
    employee_id: int = Form(...),
    pin: str = Form(...),
    db: Session = Depends(get_db),
):
    """Quick check-in: verify PIN, record rounded-down time."""
    emp = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.is_active == True,
    ).first()

    if not emp or not verify_pin(pin, emp.pin_hash):
        return JSONResponse(
            {"ok": False, "error": "Invalid PIN."},
            status_code=401,
        )


    now = datetime.now()
    today = date.today()
    rounded_time = round_down_5(now)

    # Prevent check-in from being earlier than the last check-out
    last_checkout = db.query(TimeEntry).filter(
        TimeEntry.employee_id == emp.id,
        TimeEntry.date == today,
        TimeEntry.entry_type == EntryType.check_out,
    ).order_by(TimeEntry.declared_time.desc()).first()

    if last_checkout and rounded_time < last_checkout.declared_time:
        rounded_time = last_checkout.declared_time

    entry = TimeEntry(
        employee_id=emp.id,
        date=today,
        declared_time=rounded_time,
        submission_time=now,
        entry_type=EntryType.check_in,
        location_type=LocationType.office,
        is_remote=False,
        comments="Quick check-in",
    )
    db.add(entry)
    db.commit()

    update_daily_summary(db, emp.id, today)

    log_action(
        db, action="quick_checkin", entity_type="TimeEntry",
        entity_id=entry.id, employee_id=emp.id,
        new_values={
            "declared_time": str(rounded_time),
            "submission_time": str(now),
        },
        ip_address=request.client.host if request.client else "",
    )

    # Send check-in confirmation email (non-blocking, if enabled)
    from app.services.settings import get_bool_setting
    if emp.email and get_bool_setting(db, "checkin_email_enabled"):
        import threading
        from app.services.email import send_checkin_email
        from app.services.time_calc import get_target_hours
        from app.models import LeaveRequest, LeaveStatus

        target_hrs = get_target_hours(today)
        expected_checkout = rounded_time + timedelta(hours=target_hrs)

        cutoff = today + timedelta(days=14)
        leaves = db.query(LeaveRequest).filter(
            LeaveRequest.employee_id == emp.id,
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
            args=(emp, rounded_time, expected_checkout, upcoming_leave),
            daemon=True,
        ).start()

    return JSONResponse({
        "ok": True,
        "message": f"Checked in at {rounded_time.strftime('%H:%M')}",
        "time": rounded_time.strftime("%H:%M"),
    })


@router.post("/quick-checkout")
async def quick_checkout(
    request: Request,
    employee_id: int = Form(...),
    pin: str = Form(...),
    db: Session = Depends(get_db),
):
    """Quick check-out: verify PIN, record rounded-up time."""
    emp = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.is_active == True,
    ).first()

    if not emp or not verify_pin(pin, emp.pin_hash):
        return JSONResponse(
            {"ok": False, "error": "Invalid PIN."},
            status_code=401,
        )


    today = date.today()

    # Verify there's a check-in today
    has_checkin = db.query(TimeEntry).filter(
        TimeEntry.employee_id == emp.id,
        TimeEntry.date == today,
        TimeEntry.entry_type == EntryType.check_in,
    ).first()

    if not has_checkin:
        return JSONResponse(
            {"ok": False, "error": "No check-in found for today. Check in first."},
            status_code=400,
        )

    now = datetime.now()
    rounded_time = round_up_5(now)

    entry = TimeEntry(
        employee_id=emp.id,
        date=today,
        declared_time=rounded_time,
        submission_time=now,
        entry_type=EntryType.check_out,
        location_type=LocationType.office,
        is_remote=False,
        comments="Quick check-out",
    )
    db.add(entry)
    db.commit()

    update_daily_summary(db, emp.id, today)

    log_action(
        db, action="quick_checkout", entity_type="TimeEntry",
        entity_id=entry.id, employee_id=emp.id,
        new_values={
            "declared_time": str(rounded_time),
            "submission_time": str(now),
        },
        ip_address=request.client.host if request.client else "",
    )

    return JSONResponse({
        "ok": True,
        "message": f"Checked out at {rounded_time.strftime('%H:%M')}",
        "time": rounded_time.strftime("%H:%M"),
    })


@router.get("/settings")
async def get_settings(db: Session = Depends(get_db)):
    """Return feature toggle settings for frontend use."""
    from app.services.settings import get_bool_setting
    return JSONResponse({
        "onscreen_numpad_enabled": get_bool_setting(db, "onscreen_numpad_enabled"),
        "onscreen_keyboard_enabled": get_bool_setting(db, "onscreen_keyboard_enabled"),
    })


@router.post("/verify-pin")
async def verify_pin_endpoint(
    employee_id: int = Form(...),
    pin: str = Form(...),
    db: Session = Depends(get_db),
):
    """Lightweight PIN check — returns valid: true/false without performing any action."""
    emp = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.is_active == True,
    ).first()

    if not emp or not verify_pin(pin, emp.pin_hash):
        return JSONResponse({"valid": False})

    return JSONResponse({"valid": True})


@router.get("/weapons/random")
async def get_random_weapon():
    """Return a random Lockheed weapon system from the static JSON file."""
    import os
    import json
    import random
    from pathlib import Path

    # Get path relative to this file's location
    base_dir = Path(__file__).resolve().parent.parent # app/
    json_path = base_dir / "static" / "lockheed_weapons.json"

    if not json_path.exists():
        return JSONResponse({"error": "Weapon data not found"}, status_code=404)
    
    with open(json_path, "r", encoding="utf-8") as f:
        weapons = json.load(f)
        if not weapons:
            return JSONResponse({"error": "No weapons available"}, status_code=404)
        return JSONResponse(random.choice(weapons))

