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
    """Lightweight schema migrations for new columns."""
    import sqlite3
    from app.config import BASE_DIR

    db_path = BASE_DIR / "sdc_time.db"
    if not db_path.exists():
        return

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Check if 'email' column exists in employees table
    cursor.execute("PRAGMA table_info(employees)")
    columns = [row[1] for row in cursor.fetchall()]
    if "email" not in columns:
        cursor.execute("ALTER TABLE employees ADD COLUMN email VARCHAR(200)")
        conn.commit()
        print("✅ Migration: Added 'email' column to employees table")

    conn.close()
