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
    added_beod_pref = "beod_pref_known" not in emp_cols
    if added_beod_pref:
        cursor.execute(
            "ALTER TABLE employees ADD COLUMN beod_pref_known BOOLEAN DEFAULT 0"
        )
    if "beod_pref_claim" not in emp_cols:
        cursor.execute(
            "ALTER TABLE employees ADD COLUMN beod_pref_claim BOOLEAN DEFAULT 0"
        )
    if "beod_pref_hours" not in emp_cols:
        cursor.execute(
            "ALTER TABLE employees ADD COLUMN beod_pref_hours FLOAT DEFAULT 1"
        )

    # Old contract defaults were 30 vacation / 10 sick days. Current policy is
    # 22 vacation / 15 sick work days (tracked in hours). Only rows still on
    # the old defaults are moved; a manager-set custom allowance is left alone.
    if "vacation_days_per_year" in _cols("employees"):
        cursor.execute(
            "UPDATE employees SET vacation_days_per_year = ? "
            "WHERE vacation_days_per_year = 30",
            (DEFAULT_VACATION_DAYS_PER_YEAR,),
        )
        if cursor.rowcount:
            print(f"✅ Migration: Vacation allowance 30 → {DEFAULT_VACATION_DAYS_PER_YEAR} days ({cursor.rowcount} employees)")
    if "sick_days_per_year" in _cols("employees"):
        cursor.execute(
            "UPDATE employees SET sick_days_per_year = ? "
            "WHERE sick_days_per_year = 10",
            (DEFAULT_SICK_DAYS_PER_YEAR,),
        )
        if cursor.rowcount:
            print(f"✅ Migration: Sick allowance 10 → {DEFAULT_SICK_DAYS_PER_YEAR} days ({cursor.rowcount} employees)")

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
        ds_cols = _cols("daily_summaries")
        if "beod_requested_hours" not in ds_cols:
            cursor.execute(
                "ALTER TABLE daily_summaries ADD COLUMN beod_requested_hours FLOAT DEFAULT 0"
            )
            cursor.execute(
                "UPDATE daily_summaries SET beod_requested_hours = 1 "
                "WHERE lunch_end_of_day = 1 "
                "AND (beod_requested_hours IS NULL OR beod_requested_hours = 0)"
            )
            print("✅ Migration: Added beod_requested_hours (legacy claims = 1h)")
        if added_beod_pref:
            from datetime import date as _date
            today_iso = _date.today().isoformat()
            # Last Mon–Thu before today. Claim follows that day; length follows
            # the last day they actually took BEOD (so a no-BEOD day keeps 30m).
            cursor.execute(
                """
                UPDATE employees
                SET beod_pref_known = 1,
                    beod_pref_claim = COALESCE((
                        SELECT ds.lunch_end_of_day
                        FROM daily_summaries ds
                        WHERE ds.employee_id = employees.id
                          AND ds.date < ?
                          AND CAST(strftime('%w', ds.date) AS INTEGER) BETWEEN 1 AND 4
                        ORDER BY ds.date DESC
                        LIMIT 1
                    ), 0),
                    beod_pref_hours = COALESCE((
                        SELECT CASE
                            WHEN ds.beod_requested_hours > 0 THEN ds.beod_requested_hours
                            ELSE 1.0
                        END
                        FROM daily_summaries ds
                        WHERE ds.employee_id = employees.id
                          AND ds.lunch_end_of_day = 1
                          AND CAST(strftime('%w', ds.date) AS INTEGER) BETWEEN 1 AND 4
                        ORDER BY ds.date DESC
                        LIMIT 1
                    ), 1)
                WHERE EXISTS (
                    SELECT 1 FROM daily_summaries ds
                    WHERE ds.employee_id = employees.id
                      AND ds.date < ?
                      AND CAST(strftime('%w', ds.date) AS INTEGER) BETWEEN 1 AND 4
                )
                """,
                (today_iso, today_iso),
            )
            print("✅ Migration: Remember last BEOD choice per employee")

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

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY,
            employee_id INTEGER NOT NULL,
            kind VARCHAR(20) NOT NULL,
            title VARCHAR(140) NOT NULL,
            details TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            status_note TEXT DEFAULT '',
            updated_by INTEGER,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY(employee_id) REFERENCES employees (id),
            FOREIGN KEY(updated_by) REFERENCES employees (id)
        )
        """
    )

    # Offsite can stay open (no end) until the next check-in closes it.
    if "offsite_entries" in [
        r[0] for r in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]:
        end_col = next(
            (row for row in cursor.execute("PRAGMA table_info(offsite_entries)") if row[1] == "end_time"),
            None,
        )
        if end_col is not None and end_col[3] == 1:
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.execute(
                """
                CREATE TABLE offsite_entries_open (
                    id INTEGER PRIMARY KEY,
                    employee_id INTEGER NOT NULL,
                    date DATE NOT NULL,
                    location VARCHAR(200) NOT NULL,
                    start_time DATETIME NOT NULL,
                    end_time DATETIME,
                    comments TEXT DEFAULT '',
                    submission_time DATETIME NOT NULL,
                    needs_review BOOLEAN DEFAULT 0,
                    FOREIGN KEY(employee_id) REFERENCES employees (id)
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO offsite_entries_open (
                    id, employee_id, date, location, start_time, end_time,
                    comments, submission_time, needs_review
                )
                SELECT id, employee_id, date, location, start_time, end_time,
                       comments, submission_time, needs_review
                FROM offsite_entries
                """
            )
            cursor.execute("DROP TABLE offsite_entries")
            cursor.execute("ALTER TABLE offsite_entries_open RENAME TO offsite_entries")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS ix_offsite_entries_date ON offsite_entries (date)"
            )
            cursor.execute("PRAGMA foreign_keys=ON")
            print("✅ Migration: Offsite end can stay open until check-in")

    conn.commit()
    conn.close()
