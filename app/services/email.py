import os
import random
import smtplib
import logging
import base64
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

from app.config import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM, SMTP_USE_TLS, EMAIL_ENABLED, APP_NAME,
)
from app.database import SessionLocal
from app.services.settings import get_setting

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


def _send_email(to_email: str, subject: str, html_body: str, text_body: str = "", images: list = None) -> bool:
    """Send an email via SMTP. Returns True on success, False on failure.
    
    Supports CID image embedding and Plain Text fallbacks.
    """
    if not to_email:
        return False

    # Get format setting
    db = SessionLocal()
    try:
        fmt = get_setting(db, "email_format").lower()
    finally:
        db.close()

    if not EMAIL_ENABLED:
        # Dev mode: save email as HTML file for preview
        import os
        from datetime import datetime as dt
        os.makedirs("dev_emails", exist_ok=True)
        
        # In dev mode, we convert CID images to Data URIs for local previewing
        dev_html = html_body
        if images:
            for cid, path in images:
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        ext = path.split(".")[-1]
                        b64 = base64.b64encode(f.read()).decode()
                        data_uri = f"data:image/{ext};base64,{b64}"
                        dev_html = dev_html.replace(f"cid:{cid}", data_uri)

        safe_subject = "".join(c if c.isalnum() or c in " -_" else "" for c in subject)
        filename = f"dev_emails/{dt.now().strftime('%H%M%S')}_{safe_subject[:40]}.html"
        
        wrapper = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{subject}</title>
<style>body{{background:#1e293b;padding:40px;margin:0;font-family:sans-serif;}}
.meta{{max-width:600px;margin:0 auto 16px;color:#94a3b8;font-family:monospace;font-size:13px;background:rgba(0,0,0,0.2);padding:12px;border-radius:8px;}}
.format-tag{{display:inline-block;padding:2px 6px;border-radius:4px;background:#334155;color:white;font-size:10px;margin-bottom:8px;}}
pre{{background:#0f172a;color:#cbd5e1;padding:20px;border-radius:12px;white-space:pre-wrap;border:1px solid #1e293b;}}
</style></head><body>
<div class="meta">
  <div class="format-tag">DEV PREVIEW</div><br>
  <strong>To:</strong> {to_email}<br>
  <strong>Subject:</strong> {subject}<br>
  <strong>Setting:</strong> email_format={fmt}
</div>
{"<pre>" + text_body + "</pre>" if fmt == 'text' else dev_html}
</body></html>"""

        with open(filename, "w", encoding="utf-8") as f:
            f.write(wrapper)
        logger.info(f"📧 Email saved (dev mode): {filename}  →  {to_email}: {subject}")
        return True

    try:
        # Related container (for CID images)
        msg_root = MIMEMultipart("related")
        msg_root["Subject"] = subject
        msg_root["From"] = SMTP_FROM
        msg_root["To"] = to_email

        # Alternative container (Text vs HTML)
        msg_alt = MIMEMultipart("alternative")
        msg_root.attach(msg_alt)

        # Attach Text
        if text_body:
            msg_alt.attach(MIMEText(text_body, "plain"))

        # Attach HTML (only if setting is not 'text')
        if fmt != "text":
            msg_alt.attach(MIMEText(html_body, "html"))
            
            # Attach Images for CID referencing
            if images:
                for cid, path in images:
                    if os.path.exists(path):
                        with open(path, "rb") as f:
                            img = MIMEImage(f.read())
                            img.add_header("Content-ID", f"<{cid}>")
                            img.add_header("Content-Disposition", "inline", filename=os.path.basename(path))
                            msg_root.attach(img)

        # Send
        if SMTP_USE_TLS:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)

        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)

        server.sendmail(SMTP_FROM, [to_email], msg_root.as_string())
        server.quit()
        logger.info(f"Email sent to {to_email}: {subject}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False


def send_checkin_email(employee, checkin_time: datetime, checkout_time: datetime,
                       upcoming_leave: list = None):
    """Send a check-in confirmation email to the employee."""
    if not employee.email:
        return False

    greeting = _get_greeting(employee.name, checkin_time.hour)
    ci_str = checkin_time.strftime("%H:%M")
    co_str = checkout_time.strftime("%H:%M")
    today_str = checkin_time.strftime("%A, %B %d, %Y")

    images = [
        ("app_logo", os.path.join(os.path.dirname(__file__), "..", "static", "images", "app_icon.png")),
    ]

    # ── Featured Lockheed System ──────────────────────────────────
    weapon_html = ""
    weapon_text = ""
    try:
        json_path = os.path.join(os.path.dirname(__file__), "..", "static", "lockheed_weapons.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                import json
                weapons = json.load(f)
                if weapons:
                    w = random.choice(weapons)
                    img_path = os.path.join(os.path.dirname(__file__), "..", w['image'].lstrip('/'))
                    images.append(("innovation_img", img_path))
                    
                    weapon_html = f"""
                    <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-top:24px;background-color:#1e293b;border-radius:12px;border:1px solid #334155;overflow:hidden;">
                        <tr>
                            <td>
                                <img src="cid:innovation_img" width="100%" style="display:block;border-bottom:1px solid #334155;border-radius:12px 12px 0 0;" alt="{w['name']}">
                            </td>
                        </tr>
                        <tr>
                            <td style="padding:20px;">
                                <div style="font-size:11px;font-weight:700;color:#60a5fa;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px;">🚀 Innovation Spotlight</div>
                                <div style="font-size:18px;font-weight:700;color:#ffffff;margin-bottom:8px;">{w['name']}</div>
                                <div style="font-size:14px;color:#94a3b8;line-height:1.6;margin-bottom:16px;">{w['summary']}</div>
                                <a href="{w['link']}" style="display:inline-block;font-size:14px;color:#60a5fa;text-decoration:none;font-weight:600;">Explore Technology &rarr;</a>
                            </td>
                        </tr>
                    </table>"""
                    weapon_text = f"\n\n🚀 INNOVATION SPOTLIGHT: {w['name']}\n{w['summary']}\nExplore: {w['link']}"
    except Exception as e:
        logger.error(f"Failed to load innovation spotlight: {e}")

    # Build upcoming leave section
    leave_html = ""
    leave_text = ""
    if upcoming_leave:
        leave_rows = ""
        leave_text = "\n\n📅 UPCOMING LEAVE:\n"
        for leave in upcoming_leave:
            leave_rows += f"""
            <tr>
                <td style="padding:8px 12px;border-bottom:1px solid #334155;color:#cbd5e1;font-size:13px;">{leave['start_date']} to {leave['end_date']}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #334155;color:#cbd5e1;font-size:13px;text-transform:capitalize;">{leave['leave_type']}</td>
            </tr>"""
            leave_text += f"• {leave['start_date']} to {leave['end_date']} ({leave['leave_type']})\n"

        leave_html = f"""
        <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-top:24px;background-color:#1e293b;border-radius:8px;border-left:4px solid #f59e0b;">
            <tr>
                <td style="padding:16px;">
                    <div style="font-size:14px;font-weight:600;color:#f59e0b;margin-bottom:12px;">📅 Upcoming Leave (Next 2 Weeks)</div>
                    <table width="100%" border="0" cellspacing="0" cellpadding="0">
                        {leave_rows}
                    </table>
                </td>
            </tr>
        </table>"""

    html = f"""
    <table width="100%" border="0" cellspacing="0" cellpadding="0" bgcolor="#020617" style="font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;margin:0;padding:20px;">
        <tr>
            <td align="center">
                <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width:600px;background-color:#0f172a;border-radius:16px;overflow:hidden;border:1px solid #1e293b;box-shadow:0 10px 25px rgba(0,0,0,0.4);">
                    <!-- Header -->
                    <tr>
                        <td style="background:linear-gradient(135deg,#1e40af,#7c3aed);padding:32px 40px;">
                            <table width="100%" border="0" cellspacing="0" cellpadding="0">
                                <tr>
                                    <td>
                                        <div style="font-size:24px;font-weight:700;color:#ffffff;margin-bottom:4px;">{greeting}</div>
                                        <div style="font-size:14px;color:rgba(255,255,255,0.8);">{today_str}</div>
                                    </td>
                                    <td align="right" width="60">
                                        <img src="cid:app_logo" width="60" height="60" style="border-radius:12px;border:1px solid rgba(255,255,255,0.2);">
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- Content -->
                    <tr>
                        <td style="padding:32px 40px;">
                            <table width="100%" border="0" cellspacing="0" cellpadding="12">
                                <tr>
                                    <td width="50%" bgcolor="#1e293b" style="border-radius:12px;text-align:center;padding:20px;">
                                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.1em;color:#94a3b8;margin-bottom:8px;">Checked In</div>
                                        <div style="font-size:32px;font-weight:700;color:#4ade80;font-family:monospace;">{ci_str}</div>
                                    </td>
                                    <td width="50%" bgcolor="#1e293b" style="border-radius:12px;text-align:center;padding:20px;">
                                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.1em;color:#94a3b8;margin-bottom:8px;">Expected Checkout</div>
                                        <div style="font-size:32px;font-weight:700;color:#60a5fa;font-family:monospace;">{co_str}</div>
                                    </td>
                                </tr>
                            </table>

                            {leave_html}
                            {weapon_html}

                            <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-top:32px;">
                                <tr>
                                    <td align="center" style="font-size:13px;color:#64748b;">
                                        <p>{APP_NAME} &bull; Electronic Time Keeping</p>
                                        <p style="margin-top:8px;">Have a productive and safe day!</p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>"""

    text = f"""{greeting}
    
You have successfully checked in for {today_str}.

Checked In: {ci_str}
Expected Checkout: {co_str}
{leave_text}{weapon_text}

Have a productive day!
{APP_NAME}"""

    return _send_email(
        employee.email,
        f"✅ Checked In at {ci_str} — {APP_NAME}",
        html,
        text,
        images
    )


def send_checkout_reminder(employee, checkout_time: datetime):
    """Send a checkout reminder email."""
    if not employee.email:
        return False

    greeting = _get_checkout_greeting(employee.name)
    co_str = checkout_time.strftime("%H:%M")

    images = [
        ("app_logo", os.path.join(os.path.dirname(__file__), "..", "static", "images", "app_icon.png")),
    ]

    html = f"""
    <table width="100%" border="0" cellspacing="0" cellpadding="0" bgcolor="#020617" style="font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;margin:0;padding:20px;">
        <tr>
            <td align="center">
                <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width:600px;background-color:#0f172a;border-radius:16px;overflow:hidden;border:1px solid #1e293b;box-shadow:0 10px 25px rgba(0,0,0,0.4);">
                    <!-- Header -->
                    <tr>
                        <td style="background:linear-gradient(135deg,#b45309,#d97706);padding:32px 40px;">
                            <table width="100%" border="0" cellspacing="0" cellpadding="0">
                                <tr>
                                    <td>
                                        <div style="font-size:24px;font-weight:700;color:#ffffff;margin-bottom:4px;">{greeting}</div>
                                        <div style="font-size:14px;color:rgba(255,255,255,0.8);">Checkout Reminder</div>
                                    </td>
                                    <td align="right" width="60">
                                        <img src="cid:app_logo" width="60" height="60" style="border-radius:12px;border:1px solid rgba(255,255,255,0.2);">
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- Content -->
                    <tr>
                        <td style="padding:32px 40px;text-align:center;">
                            <div style="font-size:16px;color:#cbd5e1;margin-bottom:24px;">
                                Your expected checkout time is in about <strong style="color:#fbbf24;">15 minutes</strong>.
                            </div>
                            
                            <table align="center" border="0" cellspacing="0" cellpadding="20" bgcolor="#1e293b" style="border-radius:12px;">
                                <tr>
                                    <td align="center">
                                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.1em;color:#94a3b8;margin-bottom:8px;">Checkout Time</div>
                                        <div style="font-size:48px;font-weight:700;color:#fbbf24;font-family:monospace;">{co_str}</div>
                                    </td>
                                </tr>
                            </table>

                            <div style="margin-top:24px;font-size:15px;color:#94a3b8;">
                                Don't forget to log your check-out before you leave! 👋
                            </div>

                            <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-top:32px;border-top:1px solid #1e293b;padding-top:20px;">
                                <tr>
                                    <td align="center" style="font-size:13px;color:#64748b;">
                                        {APP_NAME} &bull; Team Attendance
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>"""

    text = f"""{greeting}
    
Your expected checkout time is in about 15 minutes: {co_str}.

Don't forget to log your check-out before you leave!

{APP_NAME}"""

    return _send_email(
        employee.email,
        f"⏰ Checkout Reminder at {co_str} — {APP_NAME}",
        html,
        text,
        images
    )


def send_policy_violation_email(employee_name: str, employee_email: str, manager_emails: list,
                               entry_type: str, declared_time: datetime,
                               submission_time: datetime, threshold: int, comments: str):
    """Send an alert email when an employee exceeds the time differential threshold."""
    diff_minutes = abs((submission_time - declared_time).total_seconds()) / 60
    type_str = "Check-In" if entry_type == "check_in" else "Check-Out"
    declared_str = declared_time.strftime("%H:%M")
    actual_str = submission_time.strftime("%H:%M")
    
    subject = f"⚠️ Policy Alert: {type_str} Time Differential — {employee_name}"
    
    images = [
        ("app_logo", os.path.join(os.path.dirname(__file__), "..", "static", "images", "app_icon.png")),
    ]

    html = f"""
    <table width="100%" border="0" cellspacing="0" cellpadding="0" bgcolor="#020617" style="font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;margin:0;padding:20px;">
        <tr>
            <td align="center">
                <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width:600px;background-color:#0f172a;border-radius:16px;overflow:hidden;border:1px solid #7f1d1d;box-shadow:0 10px 25px rgba(0,0,0,0.5);">
                    <!-- Header -->
                    <tr>
                        <td style="background:linear-gradient(135deg,#7f1d1d,#b91c1c);padding:32px 40px;">
                            <table width="100%" border="0" cellspacing="0" cellpadding="0">
                                <tr>
                                    <td>
                                        <div style="font-size:24px;font-weight:700;color:#ffffff;margin-bottom:4px;">⚠️ Policy Alert</div>
                                        <div style="font-size:14px;color:rgba(255,255,255,0.8);">Threshold Exceeded</div>
                                    </td>
                                    <td align="right" width="60">
                                        <img src="cid:app_logo" width="60" height="60" style="border-radius:12px;border:1px solid rgba(255,255,255,0.2);">
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- Content -->
                    <tr>
                        <td style="padding:32px 40px;">
                            <p style="color:#cbd5e1;font-size:15px;line-height:1.6;margin-bottom:24px;">
                                A {type_str.lower()} was logged with a time difference exceeding the threshold of <strong>{threshold} minutes</strong>.
                            </p>
                            
                            <table width="100%" border="0" cellspacing="0" cellpadding="15" bgcolor="#1e293b" style="border-radius:12px;margin-bottom:24px;">
                                <tr>
                                    <td style="color:#94a3b8;font-size:14px;border-bottom:1px solid #334155;">Employee</td>
                                    <td style="color:#ffffff;font-size:14px;font-weight:600;border-bottom:1px solid #334155;">{employee_name}</td>
                                </tr>
                                <tr>
                                    <td style="color:#94a3b8;font-size:14px;border-bottom:1px solid #334155;">Action</td>
                                    <td style="color:#ffffff;font-size:14px;font-weight:600;border-bottom:1px solid #334155;">{type_str}</td>
                                </tr>
                                <tr>
                                    <td style="color:#94a3b8;font-size:14px;border-bottom:1px solid #334155;">Set Time</td>
                                    <td style="color:#fbbf24;font-size:14px;font-weight:700;border-bottom:1px solid #334155;">{declared_str}</td>
                                </tr>
                                <tr>
                                    <td style="color:#94a3b8;font-size:14px;border-bottom:1px solid #334155;">Actual Time</td>
                                    <td style="color:#60a5fa;font-size:14px;font-weight:700;border-bottom:1px solid #334155;">{actual_str}</td>
                                </tr>
                                <tr>
                                    <td style="color:#94a3b8;font-size:14px;">Differential</td>
                                    <td style="color:#f87171;font-size:14px;font-weight:700;">{int(diff_minutes)} minutes</td>
                                </tr>
                            </table>
                            
                            <table width="100%" border="0" cellspacing="0" cellpadding="16" bgcolor="#020617" style="border-radius:8px;border:1px solid #374151;">
                                <tr>
                                    <td>
                                        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px;">Employee Comment:</div>
                                        <div style="font-style:italic;color:#e2e8f0;font-size:15px;line-height:1.6;">"{comments}"</div>
                                    </td>
                                </tr>
                            </table>
                            
                            <p style="margin-top:32px;padding-top:20px;border-top:1px solid #1e293b;font-size:12px;color:#64748b;text-align:center;">
                                Automated Policy Notification from {APP_NAME}
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>"""

    text = f"""⚠️ POLICY ALERT: {type_str} Time Differential — {employee_name}

A {type_str.lower()} was logged with a differential exceeding the {threshold} minute threshold.

Employee: {employee_name}
Action: {type_str}
Set Time: {declared_str}
Actual Time: {actual_str}
Differential: {int(diff_minutes)} minutes

Comment: "{comments}"

{APP_NAME} Policy Enforcement"""
    
    # Notify Managers
    if manager_emails:
        for m_email in manager_emails:
            _send_email(m_email, subject, html, text, images)
            
    # Also notify the employee
    if employee_email:
        _send_email(employee_email, subject, html, text, images)
    
    return True


def send_past_day_modification_email(modifier_name: str, employee_name: str, target_date: str,
                                    action_description: str, comments: str, manager_emails: list,
                                    details: list = None):
    """Send a notification when a past day's record is modified."""
    subject = f"📝 Record Adjusted: {employee_name} ({target_date}) — {APP_NAME}"
    now_str = datetime.now().strftime("%B %d, %Y %H:%M")

    images = [
        ("app_logo", os.path.join(os.path.dirname(__file__), "..", "static", "images", "app_icon.png")),
    ]

    # Build details rows
    details_html = ""
    details_text = ""
    if details:
        for label, value in details:
            details_html += f"""
            <tr>
                <td style="color:#94a3b8;font-size:14px;border-bottom:1px solid #334155;">{label}</td>
                <td style="color:#ffffff;font-size:14px;font-weight:600;border-bottom:1px solid #334155;">{value}</td>
            </tr>"""
            details_text += f"{label}: {value}\n"

    html = f"""
    <table width="100%" border="0" cellspacing="0" cellpadding="0" bgcolor="#020617" style="font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;margin:0;padding:20px;">
        <tr>
            <td align="center">
                <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width:600px;background-color:#0f172a;border-radius:16px;overflow:hidden;border:1px solid #334155;box-shadow:0 10px 25px rgba(0,0,0,0.4);">
                    <!-- Header -->
                    <tr>
                        <td style="background:linear-gradient(135deg,#4338ca,#6366f1);padding:32px 40px;">
                            <table width="100%" border="0" cellspacing="0" cellpadding="0">
                                <tr>
                                    <td>
                                        <div style="font-size:24px;font-weight:700;color:#ffffff;margin-bottom:4px;">Audit Alert</div>
                                        <div style="font-size:14px;color:rgba(255,255,255,0.8);">Timesheet Modification Detected</div>
                                    </td>
                                    <td align="right" width="60">
                                        <img src="cid:app_logo" width="60" height="60" style="border-radius:12px;border:1px solid rgba(255,255,255,0.2);">
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- Content -->
                    <tr>
                        <td style="padding:32px 40px;">
                            <p style="color:#cbd5e1;font-size:15px;line-height:1.6;margin-bottom:24px;">
                                A manual correction or entry was logged for a previous workday.
                            </p>
                            
                            <table width="100%" border="0" cellspacing="0" cellpadding="15" bgcolor="#1e293b" style="border-radius:12px;margin-bottom:24px;">
                                <tr>
                                    <td style="color:#94a3b8;font-size:14px;border-bottom:1px solid #334155;">Modified Record</td>
                                    <td style="color:#ffffff;font-size:14px;font-weight:600;border-bottom:1px solid #334155;">{employee_name}</td>
                                </tr>
                                <tr>
                                    <td style="color:#94a3b8;font-size:14px;border-bottom:1px solid #334155;">Target Date</td>
                                    <td style="color:#60a5fa;font-size:14px;font-weight:700;border-bottom:1px solid #334155;">{target_date}</td>
                                </tr>
                                <tr>
                                    <td style="color:#94a3b8;font-size:14px;border-bottom:1px solid #334155;">Summary</td>
                                    <td style="color:#ffffff;font-size:14px;font-weight:600;border-bottom:1px solid #334155;">{action_description}</td>
                                </tr>
                                {details_html}
                                <tr>
                                    <td style="color:#94a3b8;font-size:14px;border-bottom:1px solid #334155;">Performed By</td>
                                    <td style="color:#cbd5e1;font-size:14px;border-bottom:1px solid #334155;">{modifier_name}</td>
                                </tr>
                                <tr>
                                    <td style="color:#94a3b8;font-size:14px;">Submission Time</td>
                                    <td style="color:#cbd5e1;font-size:14px;">{now_str}</td>
                                </tr>
                            </table>
                            
                            <table width="100%" border="0" cellspacing="0" cellpadding="16" bgcolor="#020617" style="border-radius:8px;border:1px solid #374151;">
                                <tr>
                                    <td>
                                        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px;">Justification / Comments:</div>
                                        <div style="font-style:italic;color:#e2e8f0;font-size:15px;line-height:1.6;">"{comments}"</div>
                                    </td>
                                </tr>
                            </table>
                            
                            <p style="margin-top:32px;padding-top:20px;border-top:1px solid #1e293b;font-size:12px;color:#64748b;text-align:center;">
                                Audit Notification from {APP_NAME}
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>"""

    text = f"""RECORD MODIFIED: {employee_name}

A past day record has been modified or added for {target_date}.

Employee: {employee_name}
Modified Date: {target_date}
Action: {action_description}
{details_text}Performed By: {modifier_name}
Time: {now_str}

Justification: "{comments}"

{APP_NAME} Audit System"""

    if manager_emails:
        for m_email in manager_emails:
            _send_email(m_email, subject, html, text, images)
            
    return True



