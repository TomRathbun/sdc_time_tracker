"""Background scheduler — runs periodic tasks like checkout reminders."""

import logging
import threading
import time
from datetime import date, datetime

logger = logging.getLogger(__name__)


def _check_for_upcoming_checkouts():
    """Email a one-time checkout code 15 minutes before the projected checkout."""
    from app.database import SessionLocal
    from app.models import DailySummary, Employee, Role, TimeEntry
    from app.services.time_calc import (
        BEOD_CREDIT_HOURS, BEOD_MINIMUM_HOURS,
        beod_offered_on, format_beod_length, friday_required_hours,
        projected_checkout_from_entries,
    )
    from app.services.email import send_checkout_reminder
    from app.services.email_checkout import cancel_checkout_token, issue_checkout_token
    from app.services.time_state import STATUS_CHECKED_IN, current_status
    from app.config import EMAIL_ENABLED
    from app.services.settings import get_bool_setting

    if not EMAIL_ENABLED:
        return

    db = SessionLocal()
    try:
        if not get_bool_setting(db, "checkout_reminder_enabled"):
            return

        today = date.today()
        now = datetime.now()

        employees = db.query(Employee).filter(
            Employee.is_active == True,
            Employee.role != Role.customer,
            Employee.email != None,
            Employee.email != "",
        ).all()

        for emp in employees:
            entries = db.query(TimeEntry).filter(
                TimeEntry.employee_id == emp.id,
                TimeEntry.date == today,
            ).all()
            summary = db.query(DailySummary).filter(
                DailySummary.employee_id == emp.id,
                DailySummary.date == today,
            ).first()
            beod_claimed = bool(summary and summary.lunch_end_of_day)
            if beod_claimed:
                credit = float(summary.beod_requested_hours or BEOD_CREDIT_HOURS)
            elif (
                beod_offered_on(today)
                and getattr(emp, "beod_pref_known", False)
                and emp.beod_pref_claim
            ):
                # Quick check-in does not tick the box. Use the saved routine
                # so the mail arrives 15 minutes before the usual leave time.
                beod_claimed = True
                credit = float(emp.beod_pref_hours or BEOD_CREDIT_HOURS)
            else:
                credit = 0.0
            required = friday_required_hours(db, emp.id, today)
            if credit > 0 and required - credit >= BEOD_MINIMUM_HOURS:
                required = round(required - credit, 2)
            projected = projected_checkout_from_entries(
                entries, today, beod_claimed=False,
                required_hours=required,
            )
            expected = projected.standard if projected else None
            if expected is None:
                continue

            minutes_until = (expected - now).total_seconds() / 60
            # The 15 minutes before leave, plus a short catch-up if the
            # tracker was restarted just after that time.
            if not (-30 < minutes_until <= 15):
                continue

            open_checkin = None
            for entry in sorted(entries, key=lambda row: (row.declared_time, row.id or 0)):
                if entry.entry_type.value == "check_in":
                    open_checkin = entry
                elif entry.entry_type.value == "check_out":
                    open_checkin = None
            if open_checkin is None:
                continue
            if current_status(db, emp.id, today) != STATUS_CHECKED_IN:
                continue

            token = issue_checkout_token(db, emp, open_checkin, expected)
            if token is None:
                continue

            fields = f"CODE: {token.code}\nDECLARED TIME: {expected.strftime('%H:%M')}"
            if beod_offered_on(today):
                claim = "yes" if emp.beod_pref_known and emp.beod_pref_claim else "no"
                length = format_beod_length(emp.beod_pref_hours or 1)
                fields += f"\nBEOD: {claim}\nBEOD LENGTH: {length}"

            logger.info(
                "Sending checkout reply mail to %s (checkout at %s)",
                emp.name, expected.strftime("%H:%M"),
            )
            if not send_checkout_reminder(emp, expected, token.code, fields):
                cancel_checkout_token(db, token)
                logger.error("Checkout reminder failed for %s; code withdrawn", emp.name)

    except Exception as e:
        logger.error(f"Scheduler error: {e}")
    finally:
        db.close()


def _scheduler_loop():
    """Reminders every 60s. Inbox every 60s so a checkout reply is picked up quickly."""
    logger.info("Background scheduler started (checkout reminder and inbox every 60s)")
    while True:
        try:
            _check_for_upcoming_checkouts()
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
        try:
            from app.database import SessionLocal
            from app.services.inbox import poll_inbox

            db = SessionLocal()
            try:
                count, note = poll_inbox(db)
                if count:
                    logger.info("Inbox: %s", note)
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Inbox poll error: {e}")
        try:
            from app.database import SessionLocal
            from app.services.summary_email import send_scheduled_summaries

            db = SessionLocal()
            try:
                send_scheduled_summaries(db)
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Summary email error: {e}")
        try:
            from app.database import SessionLocal
            from app.services.missed_entry import remind_missed_entries

            db = SessionLocal()
            try:
                sent = remind_missed_entries(db)
                if sent:
                    logger.info("Missed-entry reminders sent: %s", sent)
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Missed-entry reminder error: {e}")
        time.sleep(60)


def start_scheduler():
    """Start the background scheduler in a daemon thread."""
    thread = threading.Thread(target=_scheduler_loop, daemon=True, name="checkout-reminder")
    thread.start()
    logger.info("✅ Checkout reminder scheduler started")
