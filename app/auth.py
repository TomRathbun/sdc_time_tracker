"""PIN authentication and session management."""

from datetime import datetime
from typing import Optional

from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from passlib.hash import pbkdf2_sha256
from sqlalchemy.orm import Session

from app.config import SECRET_KEY, SESSION_COOKIE_NAME, SESSION_MAX_AGE
from app.models import Employee


serializer = URLSafeTimedSerializer(SECRET_KEY)


def hash_pin(pin: str) -> str:
    """Hash a PIN for storage."""
    return pbkdf2_sha256.hash(pin)


def verify_pin(pin: str, pin_hash: str) -> bool:
    """Verify a PIN against its hash."""
    return pbkdf2_sha256.verify(pin, pin_hash)


def create_session_token(employee_id: int) -> str:
    """Create a signed session token."""
    return serializer.dumps({"employee_id": employee_id})


def decode_session_token(token: str) -> Optional[dict]:
    """Decode and validate a session token."""
    try:
        return serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def get_current_employee(request: Request, db: Session) -> Optional[Employee]:
    """Get the currently authenticated employee from session cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None

    data = decode_session_token(token)
    if not data:
        return None

    employee = db.query(Employee).filter(
        Employee.id == data["employee_id"],
        Employee.is_active == True
    ).first()

    # Enforce PIN reset if required (skip for /reset-pin and /logout)
    if employee and employee.pin_needs_reset and request.url.path not in ["/reset-pin", "/logout"]:
        raise HTTPException(status_code=303, headers={"Location": "/reset-pin"})

    return employee


def require_auth(request: Request, db: Session) -> Employee:
    """Require authentication; redirect to login if not authenticated."""
    employee = get_current_employee(request, db)
    if not employee:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return employee


def require_role(employee: Employee, *roles):
    """Require the employee to have one of the specified roles."""
    if employee.role not in roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return employee
