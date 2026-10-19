"""Main FastAPI application."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_NAME, APP_VERSION
from app.database import init_db
from app.routes import auth, dashboard, time_entry, leave, admin, reports, quick_action, suggestions, inbox, changes

# Create a static directory if not exists
Path("app/static/css").mkdir(parents=True, exist_ok=True)
Path("app/static/js").mkdir(parents=True, exist_ok=True)

app = FastAPI(title=APP_NAME, version=APP_VERSION)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include routers
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(time_entry.router)
app.include_router(leave.router)
app.include_router(admin.router)
app.include_router(reports.router)
app.include_router(quick_action.router)
app.include_router(suggestions.router)
app.include_router(inbox.router)
app.include_router(changes.router)


@app.middleware("http")
async def attach_pending_approvals(request: Request, call_next):
    """Load pending approval counts for manager/supervisor nav badges."""
    request.state.pending_approvals = {
        "pending_leave": 0,
        "pending_beod": 0,
        "pending_pto": 0,
        "pending_offset": 0,
        "total": 0,
    }
    path = request.url.path or ""
    if path.startswith("/static"):
        return await call_next(request)
    if path.startswith("/api/") and not path.startswith("/api/quick"):
        return await call_next(request)
    try:
        from app.database import SessionLocal
        from app.auth import decode_session_token
        from app.config import SESSION_COOKIE_NAME
        from app.models import Employee
        from app.services.pending import get_pending_approvals, is_approver

        token = request.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            return await call_next(request)
        data = decode_session_token(token)
        if not data:
            return await call_next(request)

        db = SessionLocal()
        try:
            emp = db.query(Employee).filter(
                Employee.id == data["employee_id"],
                Employee.is_active == True,  # noqa: E712
            ).first()
            if emp and emp.role.value == "customer" and _customer_blocked(path, request.method):
                if path.startswith("/api/"):
                    return JSONResponse(
                        {"ok": False, "error": "Customers do not log time."},
                        status_code=403,
                    )
                return RedirectResponse(url="/admin/timesheet", status_code=303)
            if is_approver(emp):
                request.state.pending_approvals = get_pending_approvals(db, emp.id)
        finally:
            db.close()
    except Exception:
        pass
    return await call_next(request)


def _customer_blocked(path: str, method: str) -> bool:
    """Customers may only read the team timeline. No punches, leave, or admin."""
    if path.startswith("/time") or path.startswith("/leave") or path.startswith("/my-time"):
        return True
    if path.startswith("/api/quick"):
        return True
    if path.startswith("/reports") or path.startswith("/audit"):
        return True
    if path == "/admin" or path.startswith("/admin/"):
        return not (path == "/admin/timesheet" and method == "GET")
    return False


@app.on_event("startup")
async def startup():
    """Initialize database and seed default data on startup."""
    init_db()
    _seed_default_data()

    # Seed feature toggle settings
    from app.database import SessionLocal
    from app.services.settings import seed_settings
    db = SessionLocal()
    try:
        seed_settings(db)
        from app.services.leave_sync import trim_historical_leave
        trimmed = trim_historical_leave(db)
        if trimmed:
            print(f"Reduced {trimmed}h of leave on earlier weeks where work ran over 40.")
    finally:
        db.close()

    # Start background checkout reminder scheduler
    from app.services.scheduler import start_scheduler
    start_scheduler()


def _seed_default_data():
    """Create the live roster if the database has no employees yet."""
    from app.database import SessionLocal
    from app.models import Employee
    from app.auth import hash_pin
    from app.live_roster import LIVE_ROSTER, INITIAL_PIN

    db = SessionLocal()
    try:
        if db.query(Employee).count() == 0:
            pin_hash = hash_pin(INITIAL_PIN)
            db.add_all([
                Employee(
                    name=name,
                    pin_hash=pin_hash,
                    role=role,
                    is_active=True,
                    pin_needs_reset=True,
                )
                for name, role in LIVE_ROSTER
            ])
            db.commit()
            print(f"Seeded {len(LIVE_ROSTER)} live employees (PIN {INITIAL_PIN}, change on first login)")
    finally:
        db.close()
