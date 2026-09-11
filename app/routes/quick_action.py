"""Quick check-in / check-out routes — PIN-verified, no session required."""

import math
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import verify_pin
from app.config import BEOD_MINIMUM_HOURS
from app.models import Employee, TimeEntry, EntryType, LocationType, OffsiteEntry, PhoneSupportEntry
from app.services.time_calc import (
    update_daily_summary, get_target_hours,
    calculate_clock_hours, calculate_offsite_hours, calculate_phone_hours,
)
from app.services.time_state import (
    can_check_in, can_check_out, can_recheckout, current_status,
    last_checkout_entry, STATUS_CHECKED_OUT,
    RETURN_CHECKIN_COMMENT, RECHECKOUT_COMMENT,
)
from app.services.time_offset import offset_approved_default
from app.services.audit import log_action
from app.services.settings import get_bool_setting

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


def _had_checkout_today(db: Session, emp_id: int, today: date) -> bool:
    return last_checkout_entry(db, emp_id, today) is not None


@router.post("/quick-checkin")
async def quick_checkin(
    request: Request,
    employee_id: int = Form(...),
    pin: str = Form(...),
    db: Session = Depends(get_db),
):
    """Quick check-in: verify PIN, record rounded-down time.

    After a checkout the same day this is a return punch (called back).
    """
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

    state_err = can_check_in(db, emp.id, today)
    if state_err:
        return JSONResponse({"ok": False, "error": state_err}, status_code=400)

    rounded_time = round_down_5(now)
    returning = _had_checkout_today(db, emp.id, today)

    # Prevent check-in from being earlier than the last check-out
    last_checkout = last_checkout_entry(db, emp.id, today)
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
        comments=RETURN_CHECKIN_COMMENT if returning else "Quick check-in",
        offset_approved=True if returning else offset_approved_default(db, rounded_time, now),
    )
    db.add(entry)
    db.commit()

    update_daily_summary(db, emp.id, today)

    log_action(
        db, action="quick_return" if returning else "quick_checkin",
        entity_type="TimeEntry",
        entity_id=entry.id, employee_id=emp.id,
        new_values={
            "declared_time": str(rounded_time),
            "submission_time": str(now),
            "returning": returning,
        },
        ip_address=request.client.host if request.client else "",
    )

    # Morning check-in email only on the first punch of the day
    from app.services.settings import get_bool_setting
    if emp.email and get_bool_setting(db, "checkin_email_enabled") and not returning:
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

    if returning:
        msg = f"Returned at {rounded_time.strftime('%H:%M')} — check out when the extra work is done"
    else:
        msg = f"Checked in at {rounded_time.strftime('%H:%M')}"

    return JSONResponse({
        "ok": True,
        "message": msg,
        "time": rounded_time.strftime("%H:%M"),
        "returning": returning,
    })


def _checkout_preview_payload(db: Session, emp_id: int) -> dict:
    """Hours/time that will be recorded for a quick checkout right now."""
    today = date.today()
    now = datetime.now()
    checkout_time = round_up_5(now)
    status = current_status(db, emp_id, today)
    is_recheckout = status == STATUS_CHECKED_OUT

    entries = (
        db.query(TimeEntry)
        .filter(TimeEntry.employee_id == emp_id, TimeEntry.date == today)
        .order_by(TimeEntry.declared_time)
        .all()
    )
    offsites = (
        db.query(OffsiteEntry)
        .filter(OffsiteEntry.employee_id == emp_id, OffsiteEntry.date == today)
        .all()
    )
    phones = (
        db.query(PhoneSupportEntry)
        .filter(PhoneSupportEntry.employee_id == emp_id, PhoneSupportEntry.date == today)
        .all()
    )

    last_co = last_checkout_entry(db, emp_id, today)
    open_checkin = None
    if not is_recheckout:
        for e in reversed(entries):
            if e.entry_type == EntryType.check_in:
                open_checkin = e
                break
            if e.entry_type == EntryType.check_out:
                break

    clock = calculate_clock_hours(entries)
    session_hours = 0.0
    session_start = None
    if is_recheckout and last_co:
        session_start = last_co.declared_time
        session_hours = max(
            0.0,
            (checkout_time - last_co.declared_time).total_seconds() / 3600.0,
        )
        clock = round(clock + session_hours, 2)
    elif open_checkin:
        session_start = open_checkin.declared_time
        session_hours = max(
            0.0,
            (checkout_time - open_checkin.declared_time).total_seconds() / 3600.0,
        )
        clock = round(clock + session_hours, 2)

    offsite_h = calculate_offsite_hours(offsites)
    phone_h = calculate_phone_hours(phones)
    work = round(clock + offsite_h + phone_h, 2)
    target = get_target_hours(today)
    beod_eligible = work >= BEOD_MINIMUM_HOURS
    beod_blanket = get_bool_setting(db, "beod_blanket_approval")
    fosc_without = work
    fosc_with = round(work + 1.0, 2) if beod_eligible else work

    from app.models import DailySummary
    summary = db.query(DailySummary).filter(
        DailySummary.employee_id == emp_id,
        DailySummary.date == today,
    ).first()
    beod_already = bool(summary and summary.lunch_end_of_day)

    if is_recheckout:
        state_err = can_recheckout(db, emp_id, today, checkout_time)
    else:
        state_err = can_check_out(db, emp_id, today, declared_time=checkout_time)

    return {
        "ok": state_err is None,
        "error": state_err,
        "recheckout": is_recheckout,
        "checkout_time": checkout_time.strftime("%H:%M"),
        "checkin_time": session_start.strftime("%H:%M") if session_start else None,
        "last_checkout_time": last_co.declared_time.strftime("%H:%M") if last_co else None,
        "session_hours": round(session_hours, 2),
        "clock_hours": clock,
        "offsite_hours": offsite_h,
        "phone_hours": phone_h,
        "fosc_hours": fosc_without,
        "fosc_with_beod": fosc_with,
        "target_hours": target,
        "beod_eligible": beod_eligible,
        "beod_minimum_hours": BEOD_MINIMUM_HOURS,
        "beod_blanket": beod_blanket,
        "beod_already": beod_already,
    }


@router.get("/checkout-preview/{employee_id}")
async def checkout_preview(employee_id: int, db: Session = Depends(get_db)):
    """Preview quick-checkout time and FOSC hours (no PIN required)."""
    emp = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.is_active == True,
    ).first()
    if not emp:
        return JSONResponse({"ok": False, "error": "Employee not found."}, status_code=404)
    return JSONResponse(_checkout_preview_payload(db, emp.id))


def _record_recheckout(
    db: Session,
    emp: Employee,
    today: date,
    now: datetime,
    rounded_time: datetime,
    claim_beod: bool,
    request: Request,
):
    """Insert return check-in at last checkout + new checkout at rounded_time."""
    last_co = last_checkout_entry(db, emp.id, today)
    return_time = last_co.declared_time

    return_ci = TimeEntry(
        employee_id=emp.id,
        date=today,
        declared_time=return_time,
        submission_time=now,
        entry_type=EntryType.check_in,
        location_type=LocationType.office,
        is_remote=False,
        comments=RETURN_CHECKIN_COMMENT,
        offset_approved=True,  # synthetic punch at previous out; not a declared offset
    )
    checkout = TimeEntry(
        employee_id=emp.id,
        date=today,
        declared_time=rounded_time,
        submission_time=now,
        entry_type=EntryType.check_out,
        location_type=LocationType.office,
        is_remote=False,
        comments=RECHECKOUT_COMMENT + (" + BEOD" if claim_beod else ""),
        offset_approved=offset_approved_default(db, rounded_time, now),
    )
    db.add(return_ci)
    db.add(checkout)
    db.commit()

    beod_approved = claim_beod and get_bool_setting(db, "beod_blanket_approval")
    summary = update_daily_summary(
        db, emp.id, today,
        lunch_end_of_day=claim_beod,
        lunch_approved=beod_approved,
    )

    extra_h = round((rounded_time - return_time).total_seconds() / 3600.0, 2)
    log_action(
        db, action="quick_recheckout", entity_type="TimeEntry",
        entity_id=checkout.id, employee_id=emp.id,
        new_values={
            "return_time": str(return_time),
            "declared_time": str(rounded_time),
            "submission_time": str(now),
            "extra_hours": extra_h,
            "beod": claim_beod,
            "beod_auto_approved": beod_approved,
        },
        ip_address=request.client.host if request.client else "",
    )
    return summary, extra_h, return_time


@router.post("/quick-checkout")
async def quick_checkout(
    request: Request,
    employee_id: int = Form(...),
    pin: str = Form(...),
    beod: str = Form("false"),
    db: Session = Depends(get_db),
):
    """Quick check-out: verify PIN, record rounded-up time; optional BEOD claim.

    If already checked out, records a re-checkout extra session from the last
    checkout until now (boss called you back).
    """
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
    now = datetime.now()
    rounded_time = round_up_5(now)
    claim_beod = str(beod).lower() in ("true", "1", "on", "yes")
    status = current_status(db, emp.id, today)

    if status == STATUS_CHECKED_OUT:
        state_err = can_recheckout(db, emp.id, today, rounded_time)
        if state_err:
            return JSONResponse({"ok": False, "error": state_err}, status_code=400)
        summary, extra_h, return_time = _record_recheckout(
            db, emp, today, now, rounded_time, claim_beod, request,
        )
        msg = (
            f"Re-checked out at {rounded_time.strftime('%H:%M')} "
            f"(+{extra_h}h extra from {return_time.strftime('%H:%M')}) "
            f"· {summary.total_hours}h FOSC"
        )
        if claim_beod:
            if summary.beod_hours:
                msg += " (BEOD +1h applied)"
            elif not get_bool_setting(db, "beod_blanket_approval"):
                msg += " (BEOD requested — pending approval)"
            else:
                msg += " (BEOD claimed but under 6h worked — no credit)"
        return JSONResponse({
            "ok": True,
            "message": msg,
            "time": rounded_time.strftime("%H:%M"),
            "fosc_hours": summary.total_hours,
            "recheckout": True,
            "extra_hours": extra_h,
        })

    state_err = can_check_out(db, emp.id, today, declared_time=rounded_time)
    if state_err:
        return JSONResponse({"ok": False, "error": state_err}, status_code=400)

    entry = TimeEntry(
        employee_id=emp.id,
        date=today,
        declared_time=rounded_time,
        submission_time=now,
        entry_type=EntryType.check_out,
        location_type=LocationType.office,
        is_remote=False,
        comments="Quick check-out" + (" + BEOD" if claim_beod else ""),
        offset_approved=offset_approved_default(db, rounded_time, now),
    )
    db.add(entry)
    db.commit()

    beod_approved = claim_beod and get_bool_setting(db, "beod_blanket_approval")
    summary = update_daily_summary(
        db, emp.id, today,
        lunch_end_of_day=claim_beod,
        lunch_approved=beod_approved,
    )

    log_action(
        db, action="quick_checkout", entity_type="TimeEntry",
        entity_id=entry.id, employee_id=emp.id,
        new_values={
            "declared_time": str(rounded_time),
            "submission_time": str(now),
            "beod": claim_beod,
            "beod_auto_approved": beod_approved,
        },
        ip_address=request.client.host if request.client else "",
    )

    msg = f"Checked out at {rounded_time.strftime('%H:%M')} · {summary.total_hours}h FOSC"
    if claim_beod:
        if beod_approved and summary.beod_hours:
            msg += " (BEOD +1h applied)"
        elif claim_beod and not beod_approved:
            msg += " (BEOD requested — pending approval)"
        elif claim_beod and not summary.beod_hours:
            msg += " (BEOD claimed but under 6h worked — no credit)"

    return JSONResponse({
        "ok": True,
        "message": msg,
        "time": rounded_time.strftime("%H:%M"),
        "fosc_hours": summary.total_hours,
        "recheckout": False,
    })


@router.get("/settings")
async def get_settings(db: Session = Depends(get_db)):
    """Return feature toggle settings for frontend use."""
    return JSONResponse({
        "onscreen_numpad_enabled": get_bool_setting(db, "onscreen_numpad_enabled"),
        "onscreen_keyboard_enabled": get_bool_setting(db, "onscreen_keyboard_enabled"),
        "beod_blanket_approval": get_bool_setting(db, "beod_blanket_approval"),
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
    import json
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
        import random
        return JSONResponse(random.choice(weapons))
