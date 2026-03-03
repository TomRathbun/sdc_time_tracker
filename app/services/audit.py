"""Audit log service — tamper-evident logging of every action."""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import AuditLog


def log_action(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    employee_id: Optional[int] = None,
    old_values: Optional[dict] = None,
    new_values: Optional[dict] = None,
    ip_address: str = "",
):
    """Record an action in the audit log."""
    entry = AuditLog(
        employee_id=employee_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_values=old_values,
        new_values=new_values,
        timestamp=datetime.utcnow(),
        ip_address=ip_address,
    )
    db.add(entry)
    db.commit()
    return entry
