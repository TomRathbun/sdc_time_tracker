"""User-facing changes and the manager's team email."""

from __future__ import annotations

import logging
from datetime import date, datetime
from html import escape

from sqlalchemy.orm import Session

from app.config import APP_NAME, EMAIL_ENABLED
from app.models import Employee, FeatureChange, Role
from app.services.email import _send_email
from app.services.email_layout import render_email

logger = logging.getLogger(__name__)

AUDIENCES = ("employee", "supervisor", "manager", "customer")

# Newest user-facing behavior. Slug is stable so a restart does not add a second row.
FEATURE_CATALOG = (
    {
        "slug": "email-checkin-confirmation",
        "title": "Email check-in sends the normal confirmation",
        "added_on": date(2026, 10, 30),
        "audience": "employee,supervisor,manager",
        "summary": (
            "A check-in made by replying to the morning email now sends the same confirmation as the kiosk. "
            "That note includes the projected checkout, the week so far, and a Lockheed weapon system. "
            "The short reply still says the time was accepted."
        ),
    },
    {
        "slug": "expat-sat-fri",
        "title": "Expat week is Saturday through Friday",
        "added_on": date(2026, 10, 29),
        "audience": "employee,supervisor,manager",
        "summary": (
            "An expat work week runs Saturday through Friday. "
            "An Emirati work week stays Sunday through Saturday. "
            "My Time and the dashboard week bars follow that week. "
            "A Saturday or Sunday support call in the expat week reduces the hours required on Friday. "
            "The team timeline stays a Monday-through-Sunday grid so everyone is on the same days."
        ),
    },
    {
        "slug": "email-approve",
        "title": "Approve from email",
        "added_on": date(2026, 10, 28),
        "audience": "manager",
        "summary": (
            "Reply APPROVE to the tracker. The mail lists pending time changes, BEOD, and leave, each with a number. "
            "Reply ALL to approve the whole list, or the numbers, such as 1, 3, 5 or 2-4. "
            "Supervisors are not on the list. Anything not numbered is left pending."
        ),
    },
    {
        "slug": "email-menu",
        "title": "Start from email",
        "added_on": date(2026, 10, 27),
        "audience": "employee,supervisor,manager",
        "summary": (
            "Email the tracker with hello, or a blank message. "
            "If the address matches your account, the reply is a short menu. "
            "Employees can reply PHONE, OFFSITE, or PTO and fill in the form that comes back. "
            "PTO can be a full day or part of a day, and stays pending until a manager approves it. "
            "A manager can also reply TIMELINE, EOD, WEEKLY, or APPROVE. "
            "WEEKLY LAST sends the previous week. "
            "APPROVE sends a numbered list. Reply ALL, or numbers such as 1, 3, 5."
        ),
    },
    {
        "slug": "timeline-approve-all",
        "title": "Approve all on the timeline",
        "added_on": date(2026, 10, 27),
        "audience": "manager,supervisor",
        "summary": (
            "When a week has pending time offsets, BEOD, or leave, the timeline shows Approve all. "
            "One click approves everything still waiting on that week."
        ),
    },
    {
        "slug": "weekend-on-timeline",
        "title": "Weekend days on the timeline",
        "added_on": date(2026, 10, 26),
        "audience": "employee,supervisor,manager",
        "summary": (
            "My Time and the team timeline now include Saturday and Sunday, so a weekend support call is visible. "
            "Expats run Saturday through Friday. Emiratis run Sunday through Saturday. "
            "Weekend support inside that week reduces the hours required on Friday. "
            "A manager sets the work week on Edit employee. Everyone starts as Expat."
        ),
    },
    {
        "slug": "phone-call-date",
        "title": "Phone call date",
        "added_on": date(2026, 10, 12),
        "audience": "employee,supervisor,manager",
        "summary": (
            "A phone call can be recorded with the date of the call. "
            "A call taken at night can be entered the next morning."
        ),
    },
    {
        "slug": "same-day-checkin",
        "title": "Correct today's check-in",
        "added_on": date(2026, 10, 13),
        "audience": "employee,supervisor,manager",
        "summary": (
            "You can correct today's check-in if the time was wrong, "
            "for example to subtract 10 minutes."
        ),
    },
    {
        "slug": "customer-timeline",
        "title": "Customer timeline",
        "added_on": date(2026, 10, 14),
        "audience": "customer",
        "summary": (
            "A customer login can open the team timeline and see declared times only. "
            "Customers do not check in, and they do not see when a punch was typed."
        ),
    },
    {
        "slug": "my-time",
        "title": "My Time",
        "added_on": date(2026, 10, 15),
        "audience": "employee,supervisor,manager",
        "summary": "My Time shows your punches and totals for earlier weeks.",
    },
    {
        "slug": "manager-summaries",
        "title": "Daily and weekly summaries",
        "added_on": date(2026, 10, 15),
        "audience": "manager",
        "summary": (
            "Managers receive a weekday summary of check-ins and check-outs, "
            "and a weekly summary. The send times are under Admin, Configuration."
        ),
    },
    {
        "slug": "inbox",
        "title": "Inbox",
        "added_on": date(2026, 10, 15),
        "audience": "manager,supervisor",
        "summary": "Mail sent to the tracker account can be read under Inbox.",
    },
    {
        "slug": "checkin-email",
        "title": "Check-in email",
        "added_on": date(2026, 10, 16),
        "audience": "employee,supervisor,manager",
        "summary": (
            "The first check-in of the day sends a confirmation with your expected checkout. "
            "A later return the same day does not send another one. "
            "Your email address must be saved on your name."
        ),
    },
    {
        "slug": "checkout-reply",
        "title": "Check out by email",
        "added_on": date(2026, 10, 16),
        "audience": "employee,supervisor,manager",
        "summary": (
            "About 15 minutes before your projected checkout you get one email. "
            "The declared time is already filled in. Reply to check out. Leave the code unchanged. "
            "You may move the time by up to 15 minutes. A larger change is done on the kiosk. "
            "The confirmation can take up to 5 minutes. "
            "If you already checked out, that email is not sent."
        ),
    },
    {
        "slug": "friday-early-leave",
        "title": "Friday early leave",
        "added_on": date(2026, 10, 16),
        "audience": "employee,supervisor,manager",
        "summary": (
            "Friday is a 4-hour day. Hours worked above the Monday–Thursday target "
            "come off Friday, so you can leave early. A short day earlier in the week "
            "cancels a long one. Approved leave does not count as extra time."
        ),
    },
    {
        "slug": "missed-entry-reply",
        "title": "Missed entry reminder",
        "added_on": date(2026, 10, 19),
        "audience": "employee,supervisor,manager",
        "summary": (
            "At the end of the day, anyone who never punched in, or who is still checked in, "
            "gets one email. A missed day can be answered Vacation, Sick, COVID, or National Holiday. "
            "A missed check-out can be answered with the time and a short reason. "
            "Anything else is fixed on the dashboard. A confirmation is sent either way."
        ),
    },
    {
        "slug": "remove-full-leave",
        "title": "Remove a full day of leave",
        "added_on": date(2026, 10, 19),
        "audience": "employee,supervisor,manager",
        "summary": (
            "Leave shows a Remove button on your own requests. "
            "That clears a full day that should have been a partial day. "
            "Enter the partial hours under Partial PTO. A manager can remove an approved request from Leave approvals."
        ),
    },
    {
        "slug": "reconcile-email",
        "title": "Email weeks for Lockheed reconciliation",
        "added_on": date(2026, 10, 19),
        "audience": "manager,supervisor",
        "summary": (
            "Reports has two customer workbooks. The weekly sheet is declared in and out, BEOD, "
            "FOSC, leave, a PTO total, and an overall total. The quarterly sheet lists each employee "
            "and manager with FOSC hours and PTO hours, and a sum for both."
        ),
    },
    {
        "slug": "manager-add-beod",
        "title": "Manager can put BEOD back",
        "added_on": date(2026, 10, 19),
        "audience": "manager,supervisor",
        "summary": (
            "On the timesheet, a Monday–Thursday day that has time but no BEOD shows + BEOD. "
            "That puts the employee's usual length back and counts it, as long as the day has at least 5 hours of work."
        ),
    },
    {
        "slug": "checkin-week-progress",
        "title": "Check-in email shows the week",
        "added_on": date(2026, 10, 20),
        "audience": "employee,supervisor,manager",
        "summary": (
            "The check-in email now shows the BEOD leave time, when the checkout email will arrive, "
            "and the hours already logged Monday through Friday."
        ),
    },
    {
        "slug": "morning-checkin-email",
        "title": "Morning check-in by email",
        "added_on": date(2026, 10, 21),
        "audience": "employee,supervisor,manager",
        "summary": (
            "Fifteen minutes before your usual check-in, the tracker emails a one-time code. "
            "Reply with the time. It can be later than when you send the reply, but not more than "
            "15 minutes earlier. The code expires at noon. You get a confirmation or a rejection that says why."
        ),
    },
    {
        "slug": "missed-entry-digest",
        "title": "Daily list of missed punches",
        "added_on": date(2026, 10, 21),
        "audience": "employee,supervisor,manager",
        "summary": (
            "The missed-entry email now goes out every day and lists every open punch from the last two weeks. "
            "Each day says what is missing and how to reply. The number of days is set on the admin page."
        ),
    },
    {
        "slug": "beod-five-hours",
        "title": "BEOD qualifies after 5 hours",
        "added_on": date(2026, 10, 22),
        "audience": "employee,supervisor,manager",
        "summary": (
            "BEOD now counts after 5 hours of work, down from 6. Days already claimed are recalculated, "
            "including earlier weeks. A day under 5 hours still keeps the claim but does not get the credit."
        ),
    },
    {
        "slug": "manager-enter-pto",
        "title": "Managers can enter PTO",
        "added_on": date(2026, 10, 22),
        "audience": "manager,supervisor",
        "summary": (
            "On the team timesheet, + PTO on a day records vacation, sick, COVID, or a holiday for that employee. "
            "It is approved immediately. Full day uses that day's hours. Enter 0 to clear the day."
        ),
    },
    {
        "slug": "email-preferences",
        "title": "Choose which emails you get",
        "added_on": date(2026, 10, 22),
        "audience": "employee,supervisor,manager",
        "summary": (
            "Email in the menu turns each kind on or off for you: check-in, morning check-in, checkout, "
            "policy notices, and feature notes. Missed punches are not optional. They go out while a punch is still open. "
            "Managers can also turn off the team summaries and reports. "
            "A reply you already send still gets a confirmation. A manager can change the same switches on Edit employee."
        ),
    },
)


def seed_feature_changes(db: Session) -> None:
    """Insert catalog rows. Refresh the wording until the feature has been emailed."""
    for item in FEATURE_CATALOG:
        row = db.query(FeatureChange).filter(FeatureChange.slug == item["slug"]).first()
        if row is None:
            db.add(FeatureChange(
                slug=item["slug"],
                title=item["title"],
                summary=item["summary"],
                added_on=item["added_on"],
                audience=item["audience"],
                emailed=False,
            ))
        elif not row.emailed:
            row.title = item["title"]
            row.summary = item["summary"]
            row.added_on = item["added_on"]
            row.audience = item["audience"]
    db.commit()


def parse_audience(value: str) -> list[str]:
    found = []
    for part in (value or "").split(","):
        part = part.strip()
        if part in AUDIENCES and part not in found:
            found.append(part)
    return found


def _compose(features: list[FeatureChange]) -> tuple[str, str]:
    blocks = [f"{feature.title}\n{feature.summary}" for feature in features]
    text = (
        "Hello,\n\n"
        "Here are updates to the SDC Time Tracker.\n\n"
        + "\n\n".join(blocks)
        + f"\n\n{APP_NAME}"
    )
    html = render_email(
        "Updates",
        "<p style=\"margin:0 0 12px;\">Hello,</p>"
        "<p style=\"margin:0 0 16px;\">Here are updates to the SDC Time Tracker.</p>"
        + "".join(
            f'<p style="margin:0 0 14px;"><strong style="color:#f8fafc;">{escape(feature.title)}</strong><br>{escape(feature.summary)}</p>'
            for feature in features
        ),
        kicker=APP_NAME,
    )
    return html, text


def send_feature_mail(
    db: Session,
    features: list[FeatureChange],
    roles: list[str],
    subject: str,
) -> tuple[int, int, str]:
    """Mail one message per person. Returns (sent, missing_email, error)."""
    if not features:
        return 0, 0, "Select at least one change."
    if not roles:
        return 0, 0, "Select who should receive it."
    if not EMAIL_ENABLED:
        return 0, 0, "SMTP is not set. Restart from start-sdc.ps1."

    role_values = [Role(name) for name in roles]
    people = (
        db.query(Employee)
        .filter(Employee.is_active == True, Employee.role.in_(role_values))
        .order_by(Employee.name)
        .all()
    )
    html, text = _compose(features)
    subject = (subject or "").strip() or f"{APP_NAME} updates"
    sent = 0
    missing = 0
    for person in people:
        address = (person.email or "").strip()
        if not address:
            missing += 1
            continue
        from app.services.email_prefs import wants_email
        if not wants_email(person, "features"):
            continue
        if _send_email(address, subject, html, text):
            sent += 1
        else:
            logger.error("Feature mail was not sent to %s", address)
    if sent:
        now = datetime.now()
        for feature in features:
            feature.emailed = True
            feature.emailed_at = now
        db.commit()
    if sent == 0 and missing == len(people):
        return 0, missing, "Nobody in that group has an email address."
    if sent == 0:
        return 0, missing, "The mail was not accepted. Check the tracker window."
    return sent, missing, ""
