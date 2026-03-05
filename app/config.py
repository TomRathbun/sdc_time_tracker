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

# Application info
APP_NAME = "SDC Time Tracker"
APP_VERSION = "1.0.0"

# SMTP email settings (set via environment variables)
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "sdc-timetracker@lockheedmartin.com")
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
EMAIL_ENABLED = bool(SMTP_HOST)
