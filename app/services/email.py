"""Email service — check-in confirmation and checkout reminders."""

import random
import smtplib
import logging
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.config import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM, SMTP_USE_TLS, EMAIL_ENABLED, APP_NAME,
)

logger = logging.getLogger(__name__)

# ── Rotating greetings ──────────────────────────────────────────────

_MORNING_GREETINGS = [
    "Good morning, {name}! ☀️",
    "Rise and shine, {name}! 🌅",
    "Top of the morning, {name}!",
    "Hello {name}, great to see you today! 👋",
    "Welcome in, {name}! Let's have a productive day! 💪",
    "Hey {name}! Hope you had a great start to your day!",
    "Good morning, {name}! Ready to make it a great one? 🚀",
    "Morning, {name}! Coffee's brewed, let's go! ☕",
]

_AFTERNOON_GREETINGS = [
    "Good afternoon, {name}! 🌤️",
    "Hey {name}, hope your day is going well!",
    "Hello {name}! Afternoon check-in — you're doing great! 💪",
    "Hi {name}! The afternoon shift begins! 🎯",
]

_CHECKOUT_GREETINGS = [
    "Heads up, {name}! ⏰",
    "Friendly reminder, {name}! 🔔",
    "Hey {name}, your day is almost done! 🎉",
    "Time flies, {name}! ⌛",
    "Almost there, {name}! Just a little more! 💫",
    "Wrapping up soon, {name}! 🏁",
    "Hey {name}, finish line in sight! 🏃",
    "{name}, don't forget to check out! 📋",
]


def _get_greeting(name: str, now_hour: int = None) -> str:
    """Return a time-appropriate rotating greeting."""
    if now_hour is None:
        now_hour = datetime.now().hour
    if now_hour < 12:
        return random.choice(_MORNING_GREETINGS).format(name=name)
    else:
        return random.choice(_AFTERNOON_GREETINGS).format(name=name)


def _get_checkout_greeting(name: str) -> str:
    """Return a rotating checkout reminder greeting."""
    return random.choice(_CHECKOUT_GREETINGS).format(name=name)


def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Send an email via SMTP. Returns True on success, False on failure.

    When SMTP is not configured, saves the email as an HTML file in dev_emails/
    so you can preview it in a browser.
    """
    if not to_email:
        return False

    if not EMAIL_ENABLED:
        # Dev mode: save email as HTML file for preview
        import os
        from datetime import datetime as dt
        os.makedirs("dev_emails", exist_ok=True)
        safe_subject = "".join(c if c.isalnum() or c in " -_" else "" for c in subject)
        filename = f"dev_emails/{dt.now().strftime('%H%M%S')}_{safe_subject[:40]}.html"
        wrapper = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{subject}</title>
<style>body{{background:#1e293b;padding:40px;margin:0;}}
.meta{{max-width:500px;margin:0 auto 16px;color:#94a3b8;font-family:monospace;font-size:13px;}}</style>
</head><body>
<div class="meta">
  <strong>To:</strong> {to_email}<br>
  <strong>Subject:</strong> {subject}<br>
  <strong>Time:</strong> {dt.now().strftime('%Y-%m-%d %H:%M:%S')}
</div>
{html_body}
</body></html>"""
        with open(filename, "w", encoding="utf-8") as f:
            f.write(wrapper)
        logger.info(f"📧 Email saved (dev mode): {filename}  →  {to_email}: {subject}")
        print(f"📧 DEV EMAIL → {filename}  (open in browser to preview)")
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))

        if SMTP_USE_TLS:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)

        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)

        server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        server.quit()
        logger.info(f"Email sent to {to_email}: {subject}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False


def send_checkin_email(employee, checkin_time: datetime, checkout_time: datetime,
                       upcoming_leave: list = None):
    """Send a check-in confirmation email to the employee.

    Args:
        employee: Employee model instance (must have .email and .name)
        checkin_time: When they checked in
        checkout_time: Expected checkout time
        upcoming_leave: List of dicts with 'start_date', 'end_date', 'leave_type'
    """
    if not employee.email:
        return False

    greeting = _get_greeting(employee.name, checkin_time.hour)
    ci_str = checkin_time.strftime("%H:%M")
    co_str = checkout_time.strftime("%H:%M")
    today_str = checkin_time.strftime("%A, %B %d, %Y")

    # ── Featured Lockheed System ──────────────────────────────────
    weapon_html = ""
    try:
        import os
        import json
        json_path = os.path.join(os.path.dirname(__file__), "..", "static", "lockheed_weapons.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                weapons = json.load(f)
                if weapons:
                    w = random.choice(weapons)
                    # For local dev, we assume localhost:8888 for images
                    img_url = f"https://localhost:8888{w['image']}"
                    weapon_html = f"""
                    <div style="margin-top:24px;background:#1e293b;border-radius:12px;overflow:hidden;border:1px solid #334155;">
                        <img src="{img_url}" style="width:100%;height:180px;object-fit:cover;" alt="{w['name']}">
                        <div style="padding:16px;">
                            <div style="font-size:11px;font-weight:700;color:#60a5fa;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px;">🚀 Innovation Spotlight</div>
                            <div style="font-size:16px;font-weight:700;color:white;margin-bottom:6px;">{w['name']}</div>
                            <div style="font-size:13px;color:#94a3b8;line-height:1.5;margin-bottom:12px;">{w['summary']}</div>
                            <a href="{w['link']}" style="display:inline-block;font-size:13px;color:#60a5fa;text-decoration:none;font-weight:600;border-bottom:1px solid #60a5fa;">Explore Technology →</a>
                        </div>
                    </div>"""
    except Exception as e:
        logger.error(f"Failed to load lockheed_weapons.json: {e}")

    # Build upcoming leave section
    leave_html = ""
    if upcoming_leave:
        leave_items = ""
        for leave in upcoming_leave:
            leave_items += f"""
            <tr>
                <td style="padding:6px 12px;border-bottom:1px solid #334155;color:#cbd5e1;">
                    {leave['start_date']} → {leave['end_date']}
                </td>
                <td style="padding:6px 12px;border-bottom:1px solid #334155;color:#cbd5e1;text-transform:capitalize;">
                    {leave['leave_type']}
                </td>
            </tr>"""

        leave_html = f"""
        <div style="margin-top:20px;padding:16px;background:#1e293b;border-radius:8px;border-left:3px solid #f59e0b;">
            <div style="font-size:13px;font-weight:600;color:#f59e0b;margin-bottom:10px;">
                📅 Upcoming Leave (Next 2 Weeks)
            </div>
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
                <tr>
                    <th style="text-align:left;padding:6px 12px;border-bottom:2px solid #475569;color:#94a3b8;">Dates</th>
                    <th style="text-align:left;padding:6px 12px;border-bottom:2px solid #475569;color:#94a3b8;">Type</th>
                </tr>
                {leave_items}
            </table>
        </div>"""

    html = f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:500px;margin:0 auto;background:#0f172a;border-radius:12px;overflow:hidden;border:1px solid #1e293b;">
        <div style="background:linear-gradient(135deg,#1e40af,#7c3aed);padding:24px 28px;">
            <div style="font-size:20px;font-weight:700;color:white;">{greeting}</div>
            <div style="font-size:13px;color:rgba(255,255,255,0.7);margin-top:4px;">{today_str}</div>
        </div>
        <div style="padding:24px 28px;">
            <div style="display:flex;gap:16px;margin-bottom:20px;">
                <div style="flex:1;background:#1e293b;border-radius:8px;padding:16px;text-align:center;">
                    <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;color:#94a3b8;margin-bottom:4px;">Checked In</div>
                    <div style="font-size:28px;font-weight:700;color:#4ade80;font-family:'Courier New',monospace;">{ci_str}</div>
                </div>
                <div style="flex:1;background:#1e293b;border-radius:8px;padding:16px;text-align:center;">
                    <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;color:#94a3b8;margin-bottom:4px;">Expected Checkout</div>
                    <div style="font-size:28px;font-weight:700;color:#60a5fa;font-family:'Courier New',monospace;">{co_str}</div>
                </div>
            </div>
            {leave_html}
            {weapon_html}
            <div style="margin-top:24px;font-size:12px;color:#64748b;text-align:center;">
                {APP_NAME} • Have a productive day!
            </div>
        </div>
    </div>
    """

    return _send_email(
        employee.email,
        f"✅ Checked In at {ci_str} — {APP_NAME}",
        html,
    )


def send_checkout_reminder(employee, checkout_time: datetime):
    """Send a checkout reminder email 15 minutes before expected checkout.

    Args:
        employee: Employee model instance
        checkout_time: Expected checkout time
    """
    if not employee.email:
        return False

    greeting = _get_checkout_greeting(employee.name)
    co_str = checkout_time.strftime("%H:%M")

    html = f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:500px;margin:0 auto;background:#0f172a;border-radius:12px;overflow:hidden;border:1px solid #1e293b;">
        <div style="background:linear-gradient(135deg,#b45309,#d97706);padding:24px 28px;">
            <div style="font-size:20px;font-weight:700;color:white;">{greeting}</div>
            <div style="font-size:13px;color:rgba(255,255,255,0.7);margin-top:4px;">Checkout Reminder</div>
        </div>
        <div style="padding:24px 28px;text-align:center;">
            <div style="font-size:14px;color:#cbd5e1;margin-bottom:16px;">
                Your expected checkout time is in about <strong style="color:#fbbf24;">15 minutes</strong>.
            </div>
            <div style="background:#1e293b;border-radius:8px;padding:20px;display:inline-block;">
                <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;color:#94a3b8;margin-bottom:4px;">Checkout Time</div>
                <div style="font-size:36px;font-weight:700;color:#fbbf24;font-family:'Courier New',monospace;">{co_str}</div>
            </div>
            <div style="margin-top:20px;font-size:13px;color:#94a3b8;">
                Don't forget to log your check-out before you leave! 👋
            </div>
            <div style="margin-top:16px;font-size:12px;color:#64748b;">
                {APP_NAME}
            </div>
        </div>
    </div>
    """

    return _send_email(
        employee.email,
        f"⏰ Checkout Reminder at {co_str} — {APP_NAME}",
        html,
    )
