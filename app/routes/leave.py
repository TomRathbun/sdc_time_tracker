"""Leave management — vacation, sick, COVID sick, UAE holiday + balances."""

from datetime import date

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_employee
from app.models import LeaveRequest, LeaveType, LeaveStatus, Role
from app.services.audit import log_action
from app.services.leave_sync import apply_leave_approval, clear_full_day_leave_for_request
from app.services.leave_balance import can_request_leave, get_leave_balance, count_workdays

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _leave_page_context(request, db, employee, error=None, success=None):
    """Context for personal leave request page (no team approvals)."""
    my_requests = (
        db.query(LeaveRequest)
        .filter(LeaveRequest.employee_id == employee.id)
        .order_by(LeaveRequest.created_at.desc())
        .all()
    )
    balance = get_leave_balance(db, employee)
    return {
        "request": request,
        "employee": employee,
        "my_requests": my_requests,
        "balance": balance,
        "error": error,
        "success": success,
    }


def _pending_leave_requests(db: Session, exclude_employee_id: int | None = None):
    q = db.query(LeaveRequest).filter(LeaveRequest.status == LeaveStatus.pending)
    if exclude_employee_id is not None:
        q = q.filter(LeaveRequest.employee_id != exclude_employee_id)
    return q.order_by(LeaveRequest.created_at).all()


@router.get("/leave", response_class=HTMLResponse)
async def leave_page(request: Request, db: Session = Depends(get_db)):
    """Personal leave requests and balances (employees and managers)."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(
        "leave.html", _leave_page_context(request, db, employee)
    )


@router.get("/leave/approvals", response_class=HTMLResponse)
async def leave_approvals_page(request: Request, db: Session = Depends(get_db)):
    """Manager/supervisor page to approve or reject team leave requests."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)
    if employee.role not in (Role.manager, Role.supervisor):
        return RedirectResponse(url="/leave", status_code=303)

    pending = _pending_leave_requests(db, exclude_employee_id=None)
    # Recently decided (last 30 for context)
    recent = (
        db.query(LeaveRequest)
        .filter(LeaveRequest.status.in_([LeaveStatus.approved, LeaveStatus.rejected]))
        .order_by(LeaveRequest.created_at.desc())
        .limit(20)
        .all()
    )

    return templates.TemplateResponse("leave_approvals.html", {
        "request": request,
        "employee": employee,
        "pending_approvals": pending,
        "recent_decisions": recent,
        "error": None,
        "success": request.query_params.get("ok"),
    })


def _submit_leave(
    request: Request,
    db: Session,
    employee,
    leave_type: LeaveType,
    start_date: str,
    end_date: str,
    comments: str,
    audit_action: str,
):
    try:
        s_date = date.fromisoformat(start_date)
        e_date = date.fromisoformat(end_date)
    except ValueError:
        return templates.TemplateResponse(
            "leave.html",
            _leave_page_context(request, db, employee, error="Invalid date format."),
        )

    err = can_request_leave(db, employee, leave_type, s_date, e_date)
    if err:
        return templates.TemplateResponse(
            "leave.html",
            _leave_page_context(request, db, employee, error=err),
        )

    leave = LeaveRequest(
        employee_id=employee.id,
        leave_type=leave_type,
        start_date=s_date,
        end_date=e_date,
        status=LeaveStatus.pending,
        comments=comments,
    )
    db.add(leave)
    db.commit()

    workdays = count_workdays(s_date, e_date)
    log_action(
        db,
        action=audit_action,
        entity_type="LeaveRequest",
        entity_id=leave.id,
        employee_id=employee.id,
        new_values={
            "start_date": start_date,
            "end_date": end_date,
            "leave_type": leave_type.value,
            "workdays": workdays,
        },
        ip_address=request.client.host if request.client else "",
    )

    return templates.TemplateResponse(
        "leave.html",
        _leave_page_context(
            request,
            db,
            employee,
            success=(
                f"Submitted {leave_type.value.replace('_', ' ')} for {workdays} workday(s). "
                "Pending manager approval."
            ),
        ),
    )


@router.post("/leave/vacation", response_class=HTMLResponse)
async def submit_vacation(
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    comments: str = Form(""),
    db: Session = Depends(get_db),
):
    """Submit advance/multi-day vacation (full day = FOSC target hours per day)."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)
    return _submit_leave(
        request, db, employee, LeaveType.vacation,
        start_date, end_date, comments, "vacation_request",
    )


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
    return _submit_leave(
        request, db, employee, LeaveType.sick,
        start_date, end_date, comments, "sick_leave_request",
    )


@router.post("/leave/covid-sick", response_class=HTMLResponse)
async def submit_covid_sick(
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    comments: str = Form(""),
    db: Session = Depends(get_db),
):
    """Submit COVID sick leave (tracks against sick balance separately typed)."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)
    return _submit_leave(
        request, db, employee, LeaveType.covid_sick,
        start_date, end_date, comments, "covid_sick_request",
    )


@router.post("/leave/uae-holiday", response_class=HTMLResponse)
async def submit_uae_holiday(
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    comments: str = Form(""),
    db: Session = Depends(get_db),
):
    """Submit UAE national holiday (full day FOSC target; no vacation/sick balance)."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)
    return _submit_leave(
        request, db, employee, LeaveType.uae_holiday,
        start_date, end_date, comments, "uae_holiday_request",
    )


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

        # Full day leave hours = target (9 Mon–Thu / 4 Fri) per workday
        apply_leave_approval(db, leave)

        log_action(
            db, action="approve_leave", entity_type="LeaveRequest",
            entity_id=leave.id, employee_id=employee.id,
            old_values={"status": old_status},
            new_values={"status": "approved"},
            ip_address=request.client.host if request.client else "",
        )

    return RedirectResponse(url="/leave/approvals?ok=approved", status_code=303)


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

        clear_full_day_leave_for_request(db, leave)

        log_action(
            db, action="reject_leave", entity_type="LeaveRequest",
            entity_id=leave.id, employee_id=employee.id,
            old_values={"status": old_status},
            new_values={"status": "rejected"},
            ip_address=request.client.host if request.client else "",
        )

    return RedirectResponse(url="/leave/approvals?ok=rejected", status_code=303)
