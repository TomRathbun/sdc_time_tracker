"""Main FastAPI application."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.config import APP_NAME, APP_VERSION
from app.database import init_db
from app.routes import auth, dashboard, time_entry, leave, admin, reports

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


@app.on_event("startup")
async def startup():
    """Initialize database and seed default data on startup."""
    init_db()
    _seed_default_data()


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
