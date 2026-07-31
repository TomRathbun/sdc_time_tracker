"""Declared vs submission time offset policy."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.services.settings import get_setting


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
