"""Quick check-in / check-out routes — PIN-verified, no session required."""

import math
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import verify_pin
from app.config import BEOD_MINIMUM_HOURS
from app.models import Employee, TimeEntry, EntryType, LocationType, OffsiteEntry, PhoneSupportEntry, DailySummary
from app.services.time_calc import (
    update_daily_summary, get_target_hours,
    calculate_clock_hours, calculate_offsite_hours, calculate_phone_hours,
    beod_offered_on, normalize_beod_length, format_beod_length,
    remember_beod_pref,
)
from app.services.time_state import (
    can_check_in, can_check_out, can_recheckout, current_status,
    last_checkout_entry, STATUS_CHECKED_OUT,
    RETURN_CHECKIN_COMMENT, RECHECKOUT_COMMENT, validate_offsite_range,
)
from app.services.time_offset import (
    offset_approved_default,
    get_comment_threshold_minutes,
    offset_minutes,
)
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


def _parse_hhmm(value: str):
    """Parse 'HH:MM' into (hour, minute), or None if blank/invalid."""
    text = (value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def resolve_quick_declared(db: Session, now: datetime, declared_hhmm: str):
    """Turn an optional quick-screen time into a declared datetime.

    Returns (datetime, None) or (None, error).
    Blank means "use the automatic rounding" — caller keeps its default.
    A supplied time must be today, on a 5-minute mark, and within
    comment_threshold_minutes of the actual submission time (no reason).
    """
    parsed = _parse_hhmm(declared_hhmm)
    if parsed is None:
        if (declared_hhmm or "").strip():
            return None, "Time must be HH:MM."
        return None, None
    hour, minute = parsed
    if minute % 5 != 0:
        return None, "Time must be in 5-minute steps."
    declared = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if declared.date() != now.date():
        return None, "Quick check can only record a time today."
    threshold = get_comment_threshold_minutes(db)
    if offset_minutes(declared, now) > threshold:
        return None, (
            f"Quick check can only move the time by up to {threshold} minutes "
            "without a reason. Sign in to record a larger change."
        )
    return declared, None


def _note_adjusted(comment: str, adjusted: bool) -> str:
    if adjusted and "(adjusted)" not in comment:
        return comment + " (adjusted)"
    return comment


def _had_checkout_today(db: Session, emp_id: int, today: date) -> bool:
    return last_checkout_entry(db, emp_id, today) is not None


@router.get("/quick-clock")
async def quick_clock(db: Session = Depends(get_db)):
    """Server clock plus the default rounded punch and the no-reason window."""
    now = datetime.now()
    return JSONResponse({
        "now": now.strftime("%H:%M:%S"),
        "checkin_time": round_down_5(now).strftime("%H:%M"),
        "checkout_time": round_up_5(now).strftime("%H:%M"),
        "threshold_minutes": get_comment_threshold_minutes(db),
    })


@router.post("/quick-checkin")
async def quick_checkin(
    request: Request,
    employee_id: int = Form(...),
    pin: str = Form(...),
    declared_time: str = Form(""),
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

    chosen, time_err = resolve_quick_declared(db, now, declared_time)
    if time_err:
        return JSONResponse({"ok": False, "error": time_err}, status_code=400)
    adjusted = chosen is not None
    rounded_time = chosen if adjusted else round_down_5(now)
    returning = _had_checkout_today(db, emp.id, today)

    # Prevent check-in from being earlier than the last check-out
    last_checkout = last_checkout_entry(db, emp.id, today)
    if last_checkout and rounded_time < last_checkout.declared_time:
        if adjusted:
            return JSONResponse(
                {
                    "ok": False,
                    "error": (
                        "Check-in cannot be earlier than your last check-out "
                        f"({last_checkout.declared_time.strftime('%H:%M')})."
                    ),
                },
                status_code=400,
            )
        rounded_time = last_checkout.declared_time

    comment = RETURN_CHECKIN_COMMENT if returning else "Quick check-in"
    entry = TimeEntry(
        employee_id=emp.id,
        date=today,
        declared_time=rounded_time,
        submission_time=now,
        entry_type=EntryType.check_in,
        location_type=LocationType.office,
        is_remote=False,
        comments=_note_adjusted(comment, adjusted),
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


def _checkout_preview_payload(db: Session, emp_id: int, checkout_time: datetime = None) -> dict:
    """Hours/time that will be recorded for a quick checkout right now."""
    today = date.today()
    now = datetime.now()
    if checkout_time is None:
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
    beod_offered = beod_offered_on(today)
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
        "beod_offered": beod_offered,
        "beod_minimum_hours": BEOD_MINIMUM_HOURS,
        "beod_blanket": beod_blanket,
        "beod_already": beod_already,
    }


@router.get("/checkout-preview/{employee_id}")
async def checkout_preview(
    employee_id: int,
    time: str = "",
    db: Session = Depends(get_db),
):
    """Preview quick-checkout time and FOSC hours (no PIN required).

    Optional `time` (HH:MM) previews an adjusted punch inside the no-reason window.
    """
    emp = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.is_active == True,
    ).first()
    if not emp:
        return JSONResponse({"ok": False, "error": "Employee not found."}, status_code=404)
    override = None
    if (time or "").strip():
        now = datetime.now()
        override, time_err = resolve_quick_declared(db, now, time)
        if time_err:
            return JSONResponse({"ok": False, "error": time_err}, status_code=400)
    return JSONResponse(_checkout_preview_payload(db, emp.id, override))


def _remember_quick_beod(db: Session, emp: Employee, today: date, claim: bool, hours) -> None:
    """Save the kiosk choice unless BEOD was already on the day and the box was hidden."""
    if not beod_offered_on(today):
        return
    if not claim:
        existing = db.query(DailySummary).filter(
            DailySummary.employee_id == emp.id,
            DailySummary.date == today,
        ).first()
        if existing and existing.lunch_end_of_day:
            return
    remember_beod_pref(emp, claim, hours, today)


def _beod_status_note(claim_beod: bool, requested, summary, db: Session) -> str:
    """Suffix for the checkout toast describing the BEOD credit."""
    if not claim_beod:
        return ""
    length = format_beod_length(summary.beod_hours or requested or 1)
    if summary.beod_hours:
        return f" (BEOD +{length} applied)"
    if not get_bool_setting(db, "beod_blanket_approval"):
        asked = format_beod_length(requested or 1)
        return f" (BEOD {asked} requested — pending approval)"
    return " (BEOD claimed but under 6h worked — no credit)"


def _record_recheckout(
    db: Session,
    emp: Employee,
    today: date,
    now: datetime,
    rounded_time: datetime,
    claim_beod: bool,
    beod_hours: float | None,
    request: Request,
    adjusted: bool = False,
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
        comments=_note_adjusted(
            RECHECKOUT_COMMENT + (
                f" + BEOD {format_beod_length(beod_hours or 1)}" if claim_beod else ""
            ),
            adjusted,
        ),
        offset_approved=offset_approved_default(db, rounded_time, now),
    )
    db.add(return_ci)
    db.add(checkout)
    db.commit()

    _remember_quick_beod(db, emp, today, claim_beod, beod_hours)
    beod_approved = claim_beod and get_bool_setting(db, "beod_blanket_approval")
    summary = update_daily_summary(
        db, emp.id, today,
        lunch_end_of_day=claim_beod,
        lunch_approved=beod_approved,
        beod_requested_hours=beod_hours if claim_beod else None,
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
            "beod_hours": beod_hours if claim_beod else 0,
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
    beod_hours: str = Form("1"),
    declared_time: str = Form(""),
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
    chosen, time_err = resolve_quick_declared(db, now, declared_time)
    if time_err:
        return JSONResponse({"ok": False, "error": time_err}, status_code=400)
    adjusted = chosen is not None
    rounded_time = chosen if adjusted else round_up_5(now)
    claim_beod = str(beod).lower() in ("true", "1", "on", "yes")
    if not beod_offered_on(today):
        claim_beod = False
    requested_beod = normalize_beod_length(beod_hours) if claim_beod else None
    status = current_status(db, emp.id, today)

    if status == STATUS_CHECKED_OUT:
        state_err = can_recheckout(db, emp.id, today, rounded_time)
        if state_err:
            return JSONResponse({"ok": False, "error": state_err}, status_code=400)
        summary, extra_h, return_time = _record_recheckout(
            db, emp, today, now, rounded_time, claim_beod, requested_beod, request, adjusted,
        )
        msg = (
            f"Re-checked out at {rounded_time.strftime('%H:%M')} "
            f"(+{extra_h}h extra from {return_time.strftime('%H:%M')}) "
            f"· {summary.total_hours}h FOSC"
        )
        msg += _beod_status_note(claim_beod, requested_beod, summary, db)
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
        comments=_note_adjusted(
            "Quick check-out" + (
                f" + BEOD {format_beod_length(requested_beod or 1)}" if claim_beod else ""
            ),
            adjusted,
        ),
        offset_approved=offset_approved_default(db, rounded_time, now),
    )
    db.add(entry)
    db.commit()

    _remember_quick_beod(db, emp, today, claim_beod, requested_beod if claim_beod else beod_hours)
    beod_approved = claim_beod and get_bool_setting(db, "beod_blanket_approval")
    summary = update_daily_summary(
        db, emp.id, today,
        lunch_end_of_day=claim_beod,
        lunch_approved=beod_approved,
        beod_requested_hours=requested_beod,
    )

    log_action(
        db, action="quick_checkout", entity_type="TimeEntry",
        entity_id=entry.id, employee_id=emp.id,
        new_values={
            "declared_time": str(rounded_time),
            "submission_time": str(now),
            "beod": claim_beod,
            "beod_hours": requested_beod or 0,
            "beod_auto_approved": beod_approved,
        },
        ip_address=request.client.host if request.client else "",
    )

    msg = f"Checked out at {rounded_time.strftime('%H:%M')} · {summary.total_hours}h FOSC"
    msg += _beod_status_note(claim_beod, requested_beod, summary, db)

    return JSONResponse({
        "ok": True,
        "message": msg,
        "time": rounded_time.strftime("%H:%M"),
        "fosc_hours": summary.total_hours,
        "recheckout": False,
    })


def _parse_hhmm(today: date, raw: str):
    text = (raw or "").strip()
    try:
        hour_s, minute_s = text.split(":")
        hour, minute = int(hour_s), int(minute_s)
    except ValueError:
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return datetime(today.year, today.month, today.day, hour, minute)


@router.post("/quick-offsite")
async def quick_offsite(
    request: Request,
    employee_id: int = Form(...),
    pin: str = Form(...),
    location: str = Form(""),
    start_time: str = Form(""),
    end_time: str = Form(""),
    comments: str = Form(""),
    beod: str = Form("false"),
    beod_hours: str = Form("1"),
    db: Session = Depends(get_db),
):
    """PIN-verified offsite block from the login list. Optional BEOD claim."""
    emp = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.is_active == True,
    ).first()
    if not emp or not verify_pin(pin, emp.pin_hash):
        return JSONResponse({"ok": False, "error": "Invalid PIN."}, status_code=401)

    place = " ".join((location or "").split())
    if not place:
        return JSONResponse({"ok": False, "error": "Enter where you worked."}, status_code=400)
    if len(place) > 200:
        return JSONResponse({"ok": False, "error": "Location is too long."}, status_code=400)

    today = date.today()
    now = datetime.now()
    start = _parse_hhmm(today, start_time)
    end = _parse_hhmm(today, end_time)
    if start is None or end is None:
        return JSONResponse({"ok": False, "error": "Enter a start and end time."}, status_code=400)

    range_err = validate_offsite_range(db, emp.id, today, start, end)
    if range_err:
        return JSONResponse({"ok": False, "error": range_err}, status_code=400)

    note = (comments or "").strip()
    entry = OffsiteEntry(
        employee_id=emp.id,
        date=today,
        location=place,
        start_time=start,
        end_time=end,
        comments=note,
        submission_time=now,
        needs_review=True,
    )
    db.add(entry)
    db.commit()

    claim_beod = str(beod).lower() in ("true", "1", "on", "yes") and beod_offered_on(today)
    requested_beod = normalize_beod_length(beod_hours) if claim_beod else None
    _remember_quick_beod(db, emp, today, claim_beod, beod_hours)
    beod_approved = claim_beod and get_bool_setting(db, "beod_blanket_approval")
    summary = update_daily_summary(
        db, emp.id, today,
        lunch_end_of_day=claim_beod,
        lunch_approved=beod_approved,
        beod_requested_hours=requested_beod,
    )

    hours = round((end - start).total_seconds() / 3600.0, 2)
    log_action(
        db, action="quick_offsite", entity_type="OffsiteEntry",
        entity_id=entry.id, employee_id=emp.id,
        new_values={
            "location": place,
            "start_time": str(start),
            "end_time": str(end),
            "hours": hours,
            "comments": note,
            "beod": claim_beod,
            "beod_hours": requested_beod or 0,
        },
        ip_address=request.client.host if request.client else "",
    )

    msg = (
        f"Offsite {start.strftime('%H:%M')}–{end.strftime('%H:%M')} "
        f"at {place} · {summary.total_hours}h FOSC"
    )
    msg += _beod_status_note(claim_beod, requested_beod, summary, db)
    return JSONResponse({
        "ok": True,
        "message": msg,
        "fosc_hours": summary.total_hours,
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
