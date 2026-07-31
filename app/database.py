"""Database engine and session management."""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import DATABASE_URL


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

# Enable WAL mode and foreign keys for SQLite
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency that provides a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables."""
    from app import models  # noqa: F401 – import so models are registered
    Base.metadata.create_all(bind=engine)
    _run_migrations()


def _run_migrations():
    """Lightweight schema migrations for new columns / tables."""
    import sqlite3
    from app.config import BASE_DIR, DEFAULT_VACATION_DAYS_PER_YEAR, DEFAULT_SICK_DAYS_PER_YEAR

    db_path = BASE_DIR / "sdc_time.db"
    if not db_path.exists():
        return

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    def _cols(table: str) -> list[str]:
        cursor.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cursor.fetchall()]

    # employees
    emp_cols = _cols("employees")
    if "email" not in emp_cols:
        cursor.execute("ALTER TABLE employees ADD COLUMN email VARCHAR(200)")
        print("✅ Migration: Added 'email' column to employees table")
    if "vacation_days_per_year" not in emp_cols:
        cursor.execute(
            f"ALTER TABLE employees ADD COLUMN vacation_days_per_year FLOAT DEFAULT {DEFAULT_VACATION_DAYS_PER_YEAR}"
        )
        print("✅ Migration: Added vacation_days_per_year to employees")
    if "sick_days_per_year" not in emp_cols:
        cursor.execute(
            f"ALTER TABLE employees ADD COLUMN sick_days_per_year FLOAT DEFAULT {DEFAULT_SICK_DAYS_PER_YEAR}"
        )
        print("✅ Migration: Added sick_days_per_year to employees")

    # daily_summaries breakdown columns
    if "daily_summaries" in [
        r[0] for r in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]:
        ds_cols = _cols("daily_summaries")
        for col, sql_type, default in [
            ("clock_hours", "FLOAT", "0"),
            ("offsite_hours", "FLOAT", "0"),
            ("phone_hours", "FLOAT", "0"),
            ("beod_hours", "FLOAT", "0"),
        ]:
            if col not in ds_cols:
                cursor.execute(
                    f"ALTER TABLE daily_summaries ADD COLUMN {col} {sql_type} DEFAULT {default}"
                )
                print(f"✅ Migration: Added {col} to daily_summaries")

    # phone_support_entries table (also created via create_all; keep for older paths)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS phone_support_entries (
            id INTEGER PRIMARY KEY,
            employee_id INTEGER NOT NULL,
            date DATE NOT NULL,
            hours FLOAT NOT NULL,
            comments TEXT DEFAULT '',
            submission_time DATETIME NOT NULL,
            FOREIGN KEY(employee_id) REFERENCES employees (id)
        )
        """
    )

    # time_entries.offset_approved
    if "time_entries" in [
        r[0] for r in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]:
        te_cols = _cols("time_entries")
        if "offset_approved" not in te_cols:
            cursor.execute(
                "ALTER TABLE time_entries ADD COLUMN offset_approved BOOLEAN DEFAULT 1"
            )
            print("✅ Migration: Added offset_approved to time_entries")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tempo_weekly (
            id INTEGER PRIMARY KEY,
            employee_id INTEGER NOT NULL,
            week_start DATE NOT NULL,
            hours FLOAT NOT NULL DEFAULT 0,
            notes TEXT DEFAULT '',
            updated_at DATETIME,
            FOREIGN KEY(employee_id) REFERENCES employees (id),
            UNIQUE(employee_id, week_start)
        )
        """
    )

    conn.commit()
    conn.close()
