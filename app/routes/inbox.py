"""Manager view of mail read from the tracker Gmail account."""

from urllib.parse import quote

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_employee
from app.models import Employee, InboundEmail, Role
from app.services.inbox import poll_inbox

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _staff(request: Request, db: Session):
    employee = get_current_employee(request, db)
    if not employee or employee.role not in (Role.manager, Role.supervisor):
        return None
    return employee


@router.get("/admin/inbox", response_class=HTMLResponse)
async def inbox_page(request: Request, db: Session = Depends(get_db)):
    employee = _staff(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    messages = (
        db.query(InboundEmail)
        .order_by(InboundEmail.received_at.desc(), InboundEmail.id.desc())
        .limit(100)
        .all()
    )
    addresses = {
        (e.email or "").strip().lower(): e.name
        for e in db.query(Employee).all()
        if e.email
    }
    return templates.TemplateResponse("admin_inbox.html", {
        "request": request,
        "employee": employee,
        "messages": messages,
        "addresses": addresses,
        "notice": request.query_params.get("notice") or "",
    })


@router.post("/admin/inbox/check")
async def inbox_check_now(request: Request, db: Session = Depends(get_db)):
    employee = _staff(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)
    _count, note = poll_inbox(db)
    return RedirectResponse(url=f"/admin/inbox?notice={quote(note)}", status_code=303)
