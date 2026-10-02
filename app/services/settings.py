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
        "description": "Minutes an employee may move a punch from the actual time with no reason. Over this, a signed-in entry needs a variance reason. Quick check-in and check-out can be adjusted up to this same limit.",
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
    "login_surface_checked_in_after": {
        "value": "1300",
        "description": "Time of day (24-hour HHMM) when people who are checked in move to the top of the login list. Before this, people who have not checked in stay on top.",
    },
    "beod_blanket_approval": {
        "value": "true",
        "description": "BEOD (break end of day): auto-approve when employee checks the box (no manager step). Off = individual approval after checkout.",
    },
    "offset_variance_reasons": {
        "value": (
            "Forgot to punch\n"
            "Traffic / commute delay\n"
            "Meeting ran long\n"
            "System or kiosk issue\n"
            "Called back after leaving"
        ),
        "description": "Canned reasons for check-in/out time variances (one per line). Employees can also choose Other and type a reason.",
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
        elif existing.description != info["description"]:
            existing.description = info["description"]
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


def parse_hhmm_setting(raw: str, default_hhmm: str = "1300") -> int:
    """Parse 1300 or 13:00 into minutes from midnight. Invalid input uses the default."""
    def _minutes(text: str):
        text = (text or "").strip()
        if ":" in text:
            parts = text.split(":")
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                return None
            hour, minute = int(parts[0]), int(parts[1])
        else:
            digits = text
            if not digits.isdigit() or len(digits) not in (3, 4):
                return None
            if len(digits) == 3:
                hour, minute = int(digits[0]), int(digits[1:])
            else:
                hour, minute = int(digits[:2]), int(digits[2:])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return hour * 60 + minute

    parsed = _minutes(raw)
    if parsed is None:
        parsed = _minutes(default_hhmm)
    return 13 * 60 if parsed is None else parsed


def format_hhmm_setting(raw: str, default_hhmm: str = "1300") -> str:
    """Normalize a config time to 4-digit HHMM (1300)."""
    minutes = parse_hhmm_setting(raw, default_hhmm)
    hour, minute = divmod(minutes, 60)
    return f"{hour:02d}{minute:02d}"


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
