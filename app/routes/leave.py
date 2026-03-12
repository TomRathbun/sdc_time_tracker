"""Leave management routes — vacation requests, sick leave with upload."""

import shutil
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_employee
from app.config import UPLOAD_DIR
from app.models import LeaveRequest, LeaveType, LeaveStatus, Employee, Role
from app.services.audit import log_action

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/leave", response_class=HTMLResponse)
async def leave_page(request: Request, db: Session = Depends(get_db)):
    """Show leave management page."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    my_requests = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee.id,
    ).order_by(LeaveRequest.created_at.desc()).all()

    # If manager/supervisor, show pending requests from others
    pending_approvals = []
    if employee.role in (Role.manager, Role.supervisor):
        pending_approvals = db.query(LeaveRequest).filter(
            LeaveRequest.status == LeaveStatus.pending,
            LeaveRequest.employee_id != employee.id,
        ).order_by(LeaveRequest.created_at).all()

    return templates.TemplateResponse("leave.html", {
        "request": request,
        "employee": employee,
        "my_requests": my_requests,
        "pending_approvals": pending_approvals,
        "error": None,
        "success": None,
    })


@router.post("/leave/vacation", response_class=HTMLResponse)
async def submit_vacation(
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    comments: str = Form(""),
    db: Session = Depends(get_db),
):
    """Submit a vacation request."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    s_date = date.fromisoformat(start_date)
    e_date = date.fromisoformat(end_date)

    if e_date < s_date:
        return RedirectResponse(url="/leave", status_code=303)

    leave = LeaveRequest(
        employee_id=employee.id,
        leave_type=LeaveType.vacation,
        start_date=s_date,
        end_date=e_date,
        status=LeaveStatus.pending,
        comments=comments,
    )
    db.add(leave)
    db.commit()

    log_action(
        db, action="vacation_request", entity_type="LeaveRequest",
        entity_id=leave.id, employee_id=employee.id,
        new_values={"start_date": start_date, "end_date": end_date},
        ip_address=request.client.host if request.client else "",
    )

    return RedirectResponse(url="/leave", status_code=303)


@router.post("/leave/sick", response_class=HTMLResponse)
async def submit_sick_leave(
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    comments: str = Form(""),
    db: Session = Depends(get_db),
):
    """Submit sick leave request."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    s_date = date.fromisoformat(start_date)
    e_date = date.fromisoformat(end_date)

    if e_date < s_date:
        return RedirectResponse(url="/leave", status_code=303)

    leave = LeaveRequest(
        employee_id=employee.id,
        leave_type=LeaveType.sick,
        start_date=s_date,
        end_date=e_date,
        status=LeaveStatus.pending,
        comments=comments,
    )
    db.add(leave)
    db.commit()

    log_action(
        db, action="sick_leave_request", entity_type="LeaveRequest",
        entity_id=leave.id, employee_id=employee.id,
        new_values={
            "start_date": start_date,
            "end_date": end_date,
        },
        ip_address=request.client.host if request.client else "",
    )

    return RedirectResponse(url="/leave", status_code=303)


@router.post("/leave/{leave_id}/approve")
async def approve_leave(
    leave_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Approve a leave request (manager/supervisor only)."""
    employee = get_current_employee(request, db)
    if not employee or employee.role not in (Role.manager, Role.supervisor):
        return RedirectResponse(url="/login", status_code=303)

    leave = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).first()
    if leave and leave.status == LeaveStatus.pending:
        old_status = leave.status.value
        leave.status = LeaveStatus.approved
        leave.approved_by = employee.id
        db.commit()

        log_action(
            db, action="approve_leave", entity_type="LeaveRequest",
            entity_id=leave.id, employee_id=employee.id,
            old_values={"status": old_status},
            new_values={"status": "approved"},
            ip_address=request.client.host if request.client else "",
        )

    return RedirectResponse(url="/leave", status_code=303)


@router.post("/leave/{leave_id}/reject")
async def reject_leave(
    leave_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Reject a leave request (manager/supervisor only)."""
    employee = get_current_employee(request, db)
    if not employee or employee.role not in (Role.manager, Role.supervisor):
        return RedirectResponse(url="/login", status_code=303)

    leave = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).first()
    if leave and leave.status == LeaveStatus.pending:
        old_status = leave.status.value
        leave.status = LeaveStatus.rejected
        leave.approved_by = employee.id
        db.commit()

        log_action(
            db, action="reject_leave", entity_type="LeaveRequest",
            entity_id=leave.id, employee_id=employee.id,
            old_values={"status": old_status},
            new_values={"status": "rejected"},
            ip_address=request.client.host if request.client else "",
        )

    return RedirectResponse(url="/leave", status_code=303)
