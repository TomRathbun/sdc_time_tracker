"""Application configuration."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Database
DATABASE_URL = f"sqlite:///{BASE_DIR / 'sdc_time.db'}"

# Session / Auth
SECRET_KEY = os.environ.get("STT_SECRET_KEY", "sdc-time-tracker-secret-key-change-in-production")
SESSION_COOKIE_NAME = "stt_session"
SESSION_MAX_AGE = 8 * 60 * 60  # 8 hours

# Upload directory for doctor's notes
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Schedule rules
WEEKDAY_HOURS = {
    0: 9,  # Monday
    1: 9,  # Tuesday
    2: 9,  # Wednesday
    3: 9,  # Thursday
    4: 4,  # Friday
    5: 0,  # Saturday
    6: 0,  # Sunday
}

# Past-day entry: only allow strictly prior workdays within this lookback window
PAST_DAY_MAX_LOOKBACK_DAYS = 14

# FOSC leave entitlements (defaults; overridable per employee).
# Stated in work days. The bank is hours: one work day of entitlement is the
# weekly average (9+9+9+9+4) / 5 = 8h. A day actually taken charges that
# weekday's target (9h Mon–Thu, 4h Friday). Partial PTO charges the hours entered.
DEFAULT_VACATION_DAYS_PER_YEAR = 22.0
DEFAULT_SICK_DAYS_PER_YEAR = 15.0
LEAVE_HOURS_PER_WORK_DAY = 8.0

# BEOD (Break at End of Day): minimum real work hours before +1h paid lunch credit
BEOD_MINIMUM_HOURS = 6.0

# Application info
APP_NAME = "SDC Time Tracker"
APP_VERSION = "1.2.0"

# SMTP email settings (set via environment variables)
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "sdc-timetracker@lockheedmartin.com")
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
EMAIL_ENABLED = bool(SMTP_HOST)
