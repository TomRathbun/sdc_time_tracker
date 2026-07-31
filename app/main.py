"""Main FastAPI application."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.config import APP_NAME, APP_VERSION
from app.database import init_db
from app.routes import auth, dashboard, time_entry, leave, admin, reports, quick_action

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
    if path.startswith("/static") or path.startswith("/api/"):
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
            if is_approver(emp):
                request.state.pending_approvals = get_pending_approvals(db, emp.id)
        finally:
            db.close()
    except Exception:
        pass
    return await call_next(request)


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
    finally:
        db.close()

    # Start background checkout reminder scheduler
    from app.services.scheduler import start_scheduler
    start_scheduler()


def _seed_default_data():
    """Create default manager account if no employees exist."""
    from app.database import SessionLocal
    from app.models import Employee, Role
    from app.auth import hash_pin

    db = SessionLocal()
    try:
        count = db.query(Employee).count()
        if count == 0:
            # Create a default manager
            manager = Employee(
                name="Admin Manager",
                pin_hash=hash_pin("1234"),
                role=Role.manager,
                is_active=True,
            )
            db.add(manager)

            # Create some sample employees
            emp1 = Employee(
                name="John Smith",
                pin_hash=hash_pin("5678"),
                role=Role.employee,
                is_active=True,
            )
            emp2 = Employee(
                name="Sarah Johnson",
                pin_hash=hash_pin("9012"),
                role=Role.supervisor,
                is_active=True,
            )
            emp3 = Employee(
                name="Ahmed Al-Rashid",
                pin_hash=hash_pin("3456"),
                role=Role.employee,
                is_active=True,
            )
            db.add_all([emp1, emp2, emp3])
            db.commit()
            print("✅ Seeded default employees (Manager PIN: 1234, Supervisor PIN: 9012)")
    finally:
        db.close()
