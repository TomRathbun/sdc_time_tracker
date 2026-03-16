"""Application settings service — runtime feature toggles."""

import logging
from sqlalchemy.orm import Session

from app.models import AppSetting

logger = logging.getLogger(__name__)

# ── Default settings with descriptions ────────────────────────────────
FEATURE_DEFAULTS = {
    "checkin_email_enabled": {
        "value": "true",
        "description": "Send confirmation email when an employee checks in",
    },
    "checkout_reminder_enabled": {
        "value": "true",
        "description": "Send reminder email before expected checkout time",
    },
    "onscreen_numpad_enabled": {
        "value": "false",
        "description": "Show on-screen number pad for PIN entry (touchscreen mode)",
    },
    "onscreen_keyboard_enabled": {
        "value": "false",
        "description": "Show on-screen keyboard for comment fields (touchscreen mode)",
    },
    "comment_threshold_minutes": {
        "value": "30",
        "description": "Require comments if set time and actual time differ by more than this many minutes",
    },
    "manager_policy_alert_enabled": {
        "value": "false",
        "description": "Email managers when an employee exceeds the time differential threshold",
    },
    "email_format": {
        "value": "html",
        "description": "Markup format for emails (html or text)",
    },
    "login_names_display_count": {
        "value": "10",
        "description": "Number of names to show in the login list before scrolling (approximate)",
    },
}


def seed_settings(db: Session):
    """Ensure all default settings exist in the database."""
    for key, info in FEATURE_DEFAULTS.items():
        existing = db.query(AppSetting).filter_by(key=key).first()
        if not existing:
            setting = AppSetting(
                key=key,
                value=info["value"],
                description=info["description"],
            )
            db.add(setting)
    db.commit()


def get_setting(db: Session, key: str) -> str:
    """Get a setting value by key. Returns default if not in DB."""
    row = db.query(AppSetting).filter_by(key=key).first()
    if row:
        return row.value
    default = FEATURE_DEFAULTS.get(key)
    return default["value"] if default else ""


def get_bool_setting(db: Session, key: str) -> bool:
    """Get a boolean setting (true/false string → bool)."""
    return get_setting(db, key).lower() == "true"


def set_setting(db: Session, key: str, value: str):
    """Set a setting value, creating the row if needed."""
    row = db.query(AppSetting).filter_by(key=key).first()
    if row:
        row.value = value
    else:
        desc = FEATURE_DEFAULTS.get(key, {}).get("description", "")
        row = AppSetting(key=key, value=value, description=desc)
        db.add(row)
    db.commit()


def get_all_settings(db: Session) -> dict:
    """Return all settings as a dict of key → {value, description}."""
    seed_settings(db)  # ensure defaults exist
    rows = db.query(AppSetting).all()
    result = {}
    for row in rows:
        result[row.key] = {
            "value": row.value,
            "description": row.description or "",
            "enabled": row.value.lower() == "true",
        }
    return result
