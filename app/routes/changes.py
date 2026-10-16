"""Manager view of user-facing changes and the team email."""

from urllib.parse import quote

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_employee
from app.models import FeatureChange, Role
from app.services.audit import log_action
from app.services.feature_changes import (
    AUDIENCES, parse_audience, seed_feature_changes, send_feature_mail,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _manager(request: Request, db: Session):
    employee = get_current_employee(request, db)
    if not employee or employee.role != Role.manager:
        return None
    return employee


@router.get("/admin/changes", response_class=HTMLResponse)
async def changes_page(request: Request, db: Session = Depends(get_db)):
    employee = _manager(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)
    seed_feature_changes(db)
    rows = db.query(FeatureChange).order_by(FeatureChange.added_on.desc(), FeatureChange.id.desc()).all()
    return templates.TemplateResponse("admin_changes.html", {
        "request": request,
        "employee": employee,
        "changes": rows,
        "audiences": AUDIENCES,
        "notice": request.query_params.get("notice") or "",
        "error": request.query_params.get("error") or "",
    })


@router.post("/admin/changes/send")
async def changes_send(
    request: Request,
    db: Session = Depends(get_db),
    subject: str = Form("SDC Time Tracker updates"),
    remind: str = Form(""),
):
    employee = _manager(request, db)
    if not employee:
        return RedirectResponse(url="/login", status_code=303)
    seed_feature_changes(db)
    form = await request.form()
    if remind == "employees":
        features = [
            row for row in db.query(FeatureChange).order_by(FeatureChange.added_on).all()
            if "employee" in parse_audience(row.audience)
        ]
        roles = ["employee"]
    else:
        raw_ids = form.getlist("feature_id")
        ids = []
        for raw in raw_ids:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        features = []
        if ids:
            features = (
                db.query(FeatureChange)
                .filter(FeatureChange.id.in_(ids))
                .order_by(FeatureChange.added_on)
                .all()
            )
        roles = [role for role in form.getlist("role") if role in AUDIENCES]

    sent, missing, error = send_feature_mail(db, features, roles, subject)
    if error:
        return RedirectResponse(
            url=f"/admin/changes?error={quote(error)}", status_code=303,
        )
    log_action(
        db, action="email_feature_changes", entity_type="FeatureChange",
        employee_id=employee.id,
        new_values={
            "subject": subject,
            "roles": roles,
            "features": [row.slug for row in features],
            "sent": sent,
            "missing_email": missing,
        },
        ip_address=request.client.host if request.client else "",
    )
    note = f"Sent to {sent}."
    if missing:
        note += f" {missing} had no email address."
    return RedirectResponse(url=f"/admin/changes?notice={quote(note)}", status_code=303)
