"""Authentication routes — PIN login / logout."""

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import verify_pin, create_session_token, get_current_employee
from app.config import SESSION_COOKIE_NAME
from app.models import Employee
from app.services.audit import log_action

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    """Show employee list for login."""
    # If already logged in, redirect to dashboard
    emp = get_current_employee(request, db)
    if emp:
        return RedirectResponse(url="/", status_code=303)

    employees = db.query(Employee).filter(Employee.is_active == True).order_by(Employee.name).all()
    return templates.TemplateResponse("login.html", {
        "request": request,
        "employees": employees,
        "selected_employee": None,
        "error": None,
    })


@router.get("/login/{employee_id}", response_class=HTMLResponse)
async def login_pin_page(employee_id: int, request: Request, db: Session = Depends(get_db)):
    """Show PIN entry for a specific employee."""
    emp = get_current_employee(request, db)
    if emp:
        return RedirectResponse(url="/", status_code=303)

    selected = db.query(Employee).filter(Employee.id == employee_id, Employee.is_active == True).first()
    if not selected:
        return RedirectResponse(url="/login", status_code=303)

    employees = db.query(Employee).filter(Employee.is_active == True).order_by(Employee.name).all()
    return templates.TemplateResponse("login.html", {
        "request": request,
        "employees": employees,
        "selected_employee": selected,
        "error": None,
    })


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    employee_id: int = Form(...),
    pin: str = Form(...),
    db: Session = Depends(get_db),
):
    """Validate PIN for the selected employee and create session."""
    selected = db.query(Employee).filter(Employee.id == employee_id, Employee.is_active == True).first()
    employees = db.query(Employee).filter(Employee.is_active == True).order_by(Employee.name).all()

    if not selected or not verify_pin(pin, selected.pin_hash):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "employees": employees,
            "selected_employee": selected,
            "error": "Invalid PIN. Please try again.",
        })

    matched = selected

    # Determine redirect URL
    redirect_url = "/"
    if matched.pin_needs_reset:
        redirect_url = "/reset-pin"

    # Create session
    token = create_session_token(matched.id)
    response = RedirectResponse(url=redirect_url, status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=8 * 60 * 60,
    )

    log_action(
        db, action="login", entity_type="Employee",
        entity_id=matched.id, employee_id=matched.id,
        ip_address=request.client.host if request.client else "",
    )

    return response


@router.get("/reset-pin", response_class=HTMLResponse)
async def reset_pin_page(request: Request, db: Session = Depends(get_db)):
    """Show PIN reset form."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse("reset_pin.html", {
        "request": request,
        "employee": employee,
        "error": None,
    })


@router.post("/reset-pin", response_class=HTMLResponse)
async def reset_pin_submit(
    request: Request,
    new_pin: str = Form(...),
    confirm_pin: str = Form(...),
    db: Session = Depends(get_db),
):
    """Update employee PIN."""
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    if new_pin != confirm_pin:
        return templates.TemplateResponse("reset_pin.html", {
            "request": request,
            "employee": employee,
            "error": "PINs do not match.",
        })

    if len(new_pin) < 4:
        return templates.TemplateResponse("reset_pin.html", {
            "request": request,
            "employee": employee,
            "error": "PIN must be at least 4 digits.",
        })

    # Update PIN
    from app.auth import hash_pin
    employee.pin_hash = hash_pin(new_pin)
    employee.pin_needs_reset = False
    db.commit()

    log_action(
        db, action="reset_pin", entity_type="Employee",
        entity_id=employee.id, employee_id=employee.id,
        ip_address=request.client.host if request.client else "",
    )

    return RedirectResponse(url="/", status_code=303)


@router.get("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    """Clear session and redirect to login."""
    emp = get_current_employee(request, db)
    if emp:
        log_action(
            db, action="logout", entity_type="Employee",
            entity_id=emp.id, employee_id=emp.id,
            ip_address=request.client.host if request.client else "",
        )

    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
