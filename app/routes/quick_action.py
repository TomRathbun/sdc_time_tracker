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
