"""Pending manager approval counts for nav badges / alerts."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import DailySummary, LeaveRequest, LeaveStatus, Role, TimeEntry


def get_pending_approvals(db: Session, manager_id: int | None = None) -> dict:
    """
    Counts of items needing manager/supervisor action.

    - pending_leave: leave requests awaiting approve/reject
    - pending_beod: BEOD claimed but not approved
    - pending_pto: partial leave hours awaiting approval
    - pending_offset: declared vs submission time over threshold
    """
    pending_leave = (
        db.query(LeaveRequest)
        .filter(LeaveRequest.status == LeaveStatus.pending)
        .count()
    )
    if manager_id is not None:
        pass

    pending_beod = (
        db.query(DailySummary)
        .filter(
            DailySummary.lunch_end_of_day == True,  # noqa: E712
            DailySummary.lunch_approved == False,  # noqa: E712
        )
        .count()
    )

    pending_pto = (
        db.query(DailySummary)
        .filter(
            DailySummary.leave_hours > 0,
            DailySummary.leave_approved == False,  # noqa: E712
        )
        .count()
    )

    pending_offset = (
        db.query(TimeEntry)
        .filter(TimeEntry.offset_approved == False)  # noqa: E712
        .count()
    )

    total = pending_leave + pending_beod + pending_pto + pending_offset
    return {
        "pending_leave": pending_leave,
        "pending_beod": pending_beod,
        "pending_pto": pending_pto,
        "pending_offset": pending_offset,
        "total": total,
    }


def is_approver(employee) -> bool:
    return employee is not None and employee.role in (Role.manager, Role.supervisor)
