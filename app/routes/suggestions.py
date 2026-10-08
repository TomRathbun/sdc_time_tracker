"""Employee suggestions — changes and new features."""

from datetime import datetime

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_employee
from app.models import Employee, Role, Suggestion, SuggestionKind, SuggestionStatus
from app.services.audit import log_action

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

TITLE_MAX = 140
DETAILS_MAX = 2000
NOTE_MAX = 500

STATUS_ORDER = (
    SuggestionStatus.open,
    SuggestionStatus.planned,
    SuggestionStatus.done,
    SuggestionStatus.declined,
)


def _counts(db: Session) -> dict:
    counts = {s.value: 0 for s in STATUS_ORDER}
    for (status,) in db.query(Suggestion.status).all():
        key = status.value if hasattr(status, "value") else str(status)
        if key in counts:
            counts[key] += 1
    return counts


def _is_approver(employee: Employee) -> bool:
    return employee.role in (Role.manager, Role.supervisor)


def _page(request, db, employee, error=None, kind="change", title="", details="", status_filter="all"):
    query = db.query(Suggestion)
    if status_filter in {s.value for s in SuggestionStatus}:
        query = query.filter(Suggestion.status == SuggestionStatus(status_filter))
    rows = query.order_by(Suggestion.created_at.desc(), Suggestion.id.desc()).all()
    counts = _counts(db)
    return {
        "request": request,
        "employee": employee,
        "suggestions": rows,
        "counts": counts,
        "total": sum(counts.values()),
        "status_filter": status_filter,
        "is_approver": _is_approver(employee),
        "error": error,
        "form_kind": kind if kind in ("change", "feature") else "change",
        "form_title": title,
        "form_details": details,
    }


@router.get("/suggestions", response_class=HTMLResponse)
async def suggestions_page(
    request: Request,
    status: str = "all",
    db: Session = Depends(get_db),
):
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)
    status_filter = status if status in {s.value for s in SuggestionStatus} or status == "all" else "all"
    saved = request.query_params.get("saved") == "1"
    ctx = _page(request, db, employee, status_filter=status_filter)
    ctx["saved"] = saved
    return templates.TemplateResponse("suggestions.html", ctx)


@router.post("/suggestions", response_class=HTMLResponse)
async def submit_suggestion(
    request: Request,
    kind: str = Form("change"),
    title: str = Form(""),
    details: str = Form(""),
    db: Session = Depends(get_db),
):
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)

    title = " ".join((title or "").split())
    details = (details or "").strip()
    if kind not in ("change", "feature"):
        kind = "change"
    error = None
    if not title:
        error = "Add a short title so people can see what you are asking for."
    elif len(title) > TITLE_MAX:
        error = f"Title must be {TITLE_MAX} characters or less."
    elif not details:
        error = "Describe the change or feature."
    elif len(details) > DETAILS_MAX:
        error = f"Details must be {DETAILS_MAX} characters or less."

    if error:
        ctx = _page(
            request, db, employee,
            error=error, kind=kind, title=title, details=details,
        )
        ctx["saved"] = False
        return templates.TemplateResponse("suggestions.html", ctx)

    now = datetime.now()
    row = Suggestion(
        employee_id=employee.id,
        kind=SuggestionKind(kind),
        title=title,
        details=details,
        status=SuggestionStatus.open,
        status_note="",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    log_action(
        db,
        action="suggestion_submit",
        entity_type="Suggestion",
        entity_id=row.id,
        employee_id=employee.id,
        new_values={"kind": kind, "title": title},
        ip_address=request.client.host if request.client else "",
    )
    return RedirectResponse(url="/suggestions?saved=1", status_code=303)


@router.post("/suggestions/{suggestion_id}/status")
async def update_suggestion_status(
    suggestion_id: int,
    request: Request,
    status: str = Form(...),
    status_note: str = Form(""),
    db: Session = Depends(get_db),
):
    employee = get_current_employee(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_approver(employee):
        return RedirectResponse(url="/suggestions", status_code=303)
    if status not in {s.value for s in SuggestionStatus}:
        return RedirectResponse(url="/suggestions", status_code=303)

    row = db.query(Suggestion).filter(Suggestion.id == suggestion_id).first()
    if not row:
        return RedirectResponse(url="/suggestions", status_code=303)

    note = " ".join((status_note or "").split())
    if len(note) > NOTE_MAX:
        note = note[:NOTE_MAX]
    old = {"status": row.status.value, "status_note": row.status_note or ""}
    row.status = SuggestionStatus(status)
    row.status_note = note
    row.updated_by = employee.id
    row.updated_at = datetime.now()
    log_action(
        db,
        action="suggestion_status",
        entity_type="Suggestion",
        entity_id=row.id,
        employee_id=employee.id,
        old_values=old,
        new_values={"status": status, "status_note": note},
        ip_address=request.client.host if request.client else "",
    )
    return RedirectResponse(url="/suggestions", status_code=303)
