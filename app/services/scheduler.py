"""Background scheduler — runs periodic tasks like checkout reminders."""

import logging
import threading
import time
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

# Track which employee+date combos already got a reminder (avoid duplicates)
_sent_reminders: set = set()


def _check_for_upcoming_checkouts():
    """Scan for employees who should check out in ~15 minutes and send reminders."""
    from app.database import SessionLocal
    from app.models import Employee, TimeEntry, EntryType
    from app.services.time_calc import get_target_hours
    from app.services.email import send_checkout_reminder
    from app.config import EMAIL_ENABLED
    from app.services.settings import get_bool_setting

    if not EMAIL_ENABLED:
        return

    db = SessionLocal()
    try:
        # Check feature flag
        if not get_bool_setting(db, "checkout_reminder_enabled"):
            return

        today = date.today()
        now = datetime.now()

        employees = db.query(Employee).filter(
            Employee.is_active == True,
            Employee.email != None,
            Employee.email != "",
        ).all()

        for emp in employees:
            reminder_key = f"{emp.id}_{today.isoformat()}"
            if reminder_key in _sent_reminders:
                continue

            # Find their latest check-in today
            last_checkin = db.query(TimeEntry).filter(
                TimeEntry.employee_id == emp.id,
                TimeEntry.date == today,
                TimeEntry.entry_type == EntryType.check_in,
            ).order_by(TimeEntry.declared_time.desc()).first()

            if not last_checkin:
                continue

            # Check if they already checked out after this check-in
            last_checkout = db.query(TimeEntry).filter(
                TimeEntry.employee_id == emp.id,
                TimeEntry.date == today,
                TimeEntry.entry_type == EntryType.check_out,
                TimeEntry.declared_time > last_checkin.declared_time,
            ).first()

            if last_checkout:
                continue  # Already checked out

            # Calculate expected checkout time
            target_hours = get_target_hours(today)
            if target_hours <= 0:
                continue

            expected_checkout = last_checkin.declared_time + timedelta(hours=target_hours)

            # Send reminder if checkout is 13-17 minutes away (window to avoid missing it)
            minutes_until = (expected_checkout - now).total_seconds() / 60
            if 0 < minutes_until <= 17:
                logger.info(f"Sending checkout reminder to {emp.name} (checkout at {expected_checkout.strftime('%H:%M')})")
                send_checkout_reminder(emp, expected_checkout)
                _sent_reminders.add(reminder_key)

    except Exception as e:
        logger.error(f"Scheduler error: {e}")
    finally:
        db.close()


def _scheduler_loop():
    """Run the scheduler loop — checks every 60 seconds."""
    logger.info("📋 Background scheduler started (checkout reminders every 60s)")
    while True:
        try:
            _check_for_upcoming_checkouts()
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
        time.sleep(60)

    # Clear old reminders at midnight logic:
    # The _sent_reminders set uses date in the key, so old entries
    # won't match tomorrow. We could periodically clean but it's harmless.


def start_scheduler():
    """Start the background scheduler in a daemon thread."""
    thread = threading.Thread(target=_scheduler_loop, daemon=True, name="checkout-reminder")
    thread.start()
    logger.info("✅ Checkout reminder scheduler started")
