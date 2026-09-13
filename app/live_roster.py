"""Go-live employee list. Used on first empty-database start and by load_live_roster.py."""

from app.models import Role

# Admin in the product is Role.manager.
LIVE_ROSTER = [
    ("Jermaine Corley", Role.manager),
    ("Robert Clay", Role.employee),
    ("Michael Collier", Role.employee),
    ("Manoj Nada", Role.employee),
    ("Tom Rathbun", Role.manager),
    ("Obaid AlRomantihi", Role.employee),
    ("Athari Alzaabi", Role.employee),
    ("Shouq Al Jneibi", Role.employee),
    ("Shaima AlShouq", Role.employee),
    ("Stephan Foerster", Role.employee),
    ("Salamah Almesmari", Role.employee),
    ("Rashed Almazrouei", Role.employee),
    ("Hamed Alhammadi", Role.employee),
    ("Rawdha Almahri", Role.employee),
    ("Abdulla Alshehhi", Role.employee),
    ("Sara AlAli", Role.employee),
    ("Sultan AlDhaheri", Role.employee),
    ("Saeed AlAli", Role.employee),
    ("Khaled Alhosani", Role.employee),
    ("Meerah Alzaabi", Role.employee),
    ("Hamed Alloghani", Role.employee),
    ("Omar Eldeeb", Role.supervisor),
]

INITIAL_PIN = "1234"
