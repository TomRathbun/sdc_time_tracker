"""Apply the go-live roster to an existing database.

Use this on a server that already has demo users. A brand-new empty database
is seeded automatically on first app start from app/live_roster.py.

  uv run python load_live_roster.py
"""

from __future__ import annotations

from app.auth import hash_pin
from app.database import SessionLocal
from app.live_roster import INITIAL_PIN, LIVE_ROSTER
from app.models import AuditLog, Employee


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def main() -> None:
    wanted = {_norm(name): (name, role) for name, role in LIVE_ROSTER}
    db = SessionLocal()
    created = 0
    updated = 0
    deactivated: list[str] = []

    try:
        by_norm = {_norm(e.name): e for e in db.query(Employee).all()}
        pin_hash = hash_pin(INITIAL_PIN)

        for name, role in LIVE_ROSTER:
            existing = by_norm.get(_norm(name))
            if existing:
                existing.name = name
                existing.role = role
                existing.is_active = True
                existing.pin_hash = pin_hash
                existing.pin_needs_reset = True
                updated += 1
                continue
            emp = Employee(
                name=name,
                pin_hash=pin_hash,
                role=role,
                is_active=True,
                pin_needs_reset=True,
            )
            db.add(emp)
            created += 1
            by_norm[_norm(name)] = emp

        db.flush()

        for emp in db.query(Employee).all():
            if _norm(emp.name) not in wanted and emp.is_active:
                emp.is_active = False
                deactivated.append(emp.name)

        n_audit = db.query(AuditLog).delete()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"Created {created}  Updated {updated}  Deactivated {len(deactivated)}  Audit cleared {n_audit}")
    if deactivated:
        print("Deactivated:", ", ".join(deactivated))
    print(f"All live accounts use PIN {INITIAL_PIN} and must change it on first login.")


if __name__ == "__main__":
    main()
