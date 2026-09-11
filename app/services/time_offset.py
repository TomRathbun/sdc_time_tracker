"""Declared vs submission time offset policy."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.services.settings import get_setting

OTHER_VARIANCE_VALUE = "__other__"

DEFAULT_VARIANCE_REASONS = [
    "Forgot to punch",
    "Traffic / commute delay",
    "Meeting ran long",
    "System or kiosk issue",
    "Called back after leaving",
]


def get_comment_threshold_minutes(db: Session) -> int:
    try:
        return int(get_setting(db, "comment_threshold_minutes"))
    except (ValueError, TypeError):
        return 30


def offset_minutes(declared: datetime, submission: datetime) -> float:
    return abs((submission - declared).total_seconds()) / 60.0


def needs_offset_approval(db: Session, declared: datetime, submission: datetime) -> bool:
    """True when |declared − submission| exceeds configured threshold."""
    return offset_minutes(declared, submission) > get_comment_threshold_minutes(db)


def offset_approved_default(db: Session, declared: datetime, submission: datetime) -> bool:
    """New entries under threshold are auto-approved; over threshold need manager."""
    return not needs_offset_approval(db, declared, submission)


def get_variance_reasons(db: Session) -> List[str]:
    """Canned variance reasons from admin config (Other is always added in the UI)."""
    raw = get_setting(db, "offset_variance_reasons") or ""
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    reasons = [ln for ln in lines if ln.lower() != "other"]
    return reasons if reasons else list(DEFAULT_VARIANCE_REASONS)


def parse_variance_reason(
    choice: str,
    other_text: str,
    canned: Optional[List[str]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a canned/Other variance reason.

    Returns (reason, None) or (None, error).
    """
    choice = (choice or "").strip()
    other = (other_text or "").strip()
    if not choice:
        return None, "Select a reason for the time difference."
    if choice == OTHER_VARIANCE_VALUE or choice.lower() == "other":
        if not other:
            return None, "Please type a reason when you select Other."
        return other, None
    canned = canned or []
    if canned and choice not in canned:
        return None, "Select a reason from the list, or Other."
    return choice, None


def resolve_offset_comment(
    db: Session,
    declared: datetime,
    submission: datetime,
    comments: str = "",
    variance_reason: str = "",
    variance_other: str = "",
) -> Tuple[str, Optional[str]]:
    """Comment to store on the punch. Over threshold requires a canned/Other reason."""
    if needs_offset_approval(db, declared, submission):
        text, err = parse_variance_reason(
            variance_reason, variance_other, get_variance_reasons(db),
        )
        return (text or ""), err
    return (comments or "").strip(), None


def reject_time_offset(entry) -> dict:
    """Disapprove a variance: declared time reverts to the actual submission time."""
    old_declared = entry.declared_time
    actual = entry.submission_time
    entry.declared_time = actual
    entry.offset_approved = True
    return {
        "entry_id": entry.id,
        "old_declared": str(old_declared),
        "new_declared": str(actual),
    }
