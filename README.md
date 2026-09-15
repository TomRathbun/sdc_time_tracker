# SDC Time Tracker

Electronic Time and Attendance System (ETAS) for SDC staff on the FOSC (Follow-On Support Contract). Dark-themed FastAPI app with check-in/out, leave, manager approvals, TEMPO variance, and quarterly FOSC Excel export.

## Key features

### Timekeeping
- **Quick check-in / check-out** — one-tap entry from login or dashboard (office, remote, offsite)
- **Re-check out** — after you have already left, punch out again to capture extra time (boss called you back). Records a return session from the previous checkout until now. Also available on past-day as an extra session.
- **Projected checkout** — after check-in, login and dashboard show expected out time (9h Mon–Thu, 8h with BEOD, 4h Friday). Clears after checkout or midnight. Friday quick checkout hides BEOD.
- **Past-day entry** — log or correct previous workdays (manager rules apply)
- **Offsite / remote work** — dedicated offsite logging with gap detection
- **Phone support hours** — additive hours that roll into FOSC totals
- **Live dashboard progress** — daily/weekly targets (default **9h Mon–Thu, 4h Fri**), including in-progress open shifts

### FOSC rules
- **Paid lunch** inside the scheduled work window
- **BEOD** (end-of-day credit) — optional blanket or per-request, with minimum raw hours (default ≥6h)
- **FOSC day total** = clock + phone + offsite + BEOD credit
- **Leave types** — vacation, sick, COVID sick, UAE national holiday  
  Full leave day = target hours for that weekday (9h or 4h Friday)
- **Leave balances** — defaults (e.g. 30 vacation / 10 sick) with pending requests reserving days
- **Projected vacation schedule** — printable customer copy + Excel (`/reports/vacation-schedule`): roster, timeline, coverage overlap, signature block. Approved-only by default; pending optional.
- **Declared vs submission** — when declared time differs from device submission beyond a threshold, the employee picks a **canned reason** (or Other). Managers see the reason on hover, then **Approve** (keep declared time) or **Reject** (revert to actual punch time). Reasons are configurable in Admin → Configuration.

### Manager / admin
- **Admin timesheet** — declared vs submitted, offsets, BEOD, leave, and future weeks. Continuous re-checkout sessions squash to first in / last out; a real gap (e.g. doctor) stays as separate punches.
- **Leave approvals** — dedicated `/leave/approvals` page (separate from request UI)
- **Config** — BEOD, schedule, thresholds, variance reasons, and related settings
- **Audit trail** — change history and policy alerts
- **Production data reset** — clear audit log, time/leave data, and/or extra employees before go-live (`/admin/data-reset`)

### Reports & FOSC export
- **TEMPO import** — weekly hours charged in Lockheed TEMPO (CSV / form)
- **FOSC weekly workbook** — `Time Keeping Sheet (Wk1)` + **Discrepancy Tracker**
- **FOSC quarterly package** — one week sheet per Monday in the quarter + Discrepancy Tracker
- **Discrepancy Tracker** — contract-style matrix (Wk1–Wk14):  
  `INDEX`/`MATCH` into each week sheet column **M** (*Hours to Reduce*)  
  Formula: only **shortfalls** where base hours &lt; TEMPO (`IF(SDC−TEMPO>0, 0, SDC−TEMPO)`)

### Tactical Library
- **Lockheed Martin innovations gallery** — products and heritage milestones on login / `/innovations`
- Random **innovation spotlight** in notification emails
- Curated image + summary pairs (fighters, rotary wing, missiles, space, history)

### Systems engineering (training)
- **`/systems-engineering`** — public teaching page derived from this app’s real design
- Context &amp; architecture diagrams, functional/NFR requirements with IDs
- Acceptance criteria (Given/When/Then), sequence diagrams, state charts (punch, leave, offset)
- BEOD decision logic, FOSC Excel interface notes, requirements traceability, V&amp;V, exercises
- Linked from the login page and main navigation (for mentoring new engineers)

## Tech stack

| Layer | Choice |
|--------|--------|
| Backend | Python 3.11+, FastAPI |
| Database | SQLite + SQLAlchemy |
| Frontend | Jinja2, Tailwind CSS, HTMX (local copies under `app/static/js/`) |
| Excel export | openpyxl |
| Tooling | `uv` (recommended) |

## Installation

**New kiosk/server PC (Windows or RHEL):** follow **[INSTALL.md](INSTALL.md)** — clone, `uv sync`, firewall, first start, auto-start, Tailscale, backups.

Quick local install:

```bash
cd sdc_time_tracker
uv sync
# or: pip install -r requirements.txt
```

## Run

```bash
uv run python run.py --no-ssl
```

App: [http://localhost:8888](http://localhost:8888)

With TLS (certs under `certs/`):

```bash
uv run python run.py
```

## Go-live / work server

The live staff list lives in git (`app/live_roster.py`). **Do not commit `sdc_time.db`.**

**Empty database (first start):** the app seeds all staff automatically.

**Database already has demo users:**

```bash
uv run python load_live_roster.py
```

That adds/updates the roster, deactivates leftover demo names, and clears the audit log.

| | |
|---|---|
| Initial PIN | `1234` for everyone |
| First login | staff **must** set a new PIN |
| Admin | Jermaine Corley, Tom Rathbun (role: manager) |
| Supervisor | Omar Eldeeb |
| Everyone else | employee |

To wipe the audit log later: log in as a manager → **Administration → Data reset** → **Clear audit log** → type `RESET`. Same page can also clear test punches.

### Shared kiosk computer

PIN fields are marked so browsers and password managers should not save or autofill them.

On the shared Opera/Chrome profile still turn **off** password saving:

**Settings → Privacy & security → Autofill → Passwords** → disable **Save passwords** and **Auto sign-in**.

A dedicated kiosk/guest profile is best.

## Typical production flow

1. Deploy, then confirm the live roster (first start, or `load_live_roster.py`)  
2. Staff check in/out with PIN `1234` and immediately set a personal PIN  
3. Log phone support, offsite, and leave as needed  
4. Managers approve leave, offsets, and review timesheets  
5. Import **TEMPO** weekly hours on **Reports**  
6. Export **FOSC weekly** or **quarterly** package for contract submission  
7. Print the **projected vacation schedule** (Reports → Open & print) for the customer  
8. Open **Discrepancy Tracker** for base vs TEMPO shortfalls  

## Training new engineers

Open **[Systems Engineering](http://localhost:8888/systems-engineering)** (also linked from the login page).

That page is a living SE pack for this product: stakeholders, architecture, shall-requirements with acceptance criteria, sequence diagrams, state charts, BEOD logic, export ICD notes, traceability, and practice exercises.

## Remote access (Tailscale)

1. Install Tailscale on host and client  
2. Host IP: `tailscale ip -4`  
3. Browse `http://[TAILSCALE-IP]:8888`  

## Project layout (high level)

```
app/
  live_roster.py         # go-live names and roles
  models.py              # employees, entries, leave, TEMPO, summaries
  routes/                # auth, dashboard, time, leave, admin, reports
  services/
    fosc_export.py       # weekly/quarterly Excel + Discrepancy Tracker
    time_state.py        # check-in state machine
    leave_balance.py     # entitlements & pending
    leave_sync.py        # leave → DailySummary
    vacation_schedule.py # customer vacation print / Excel
    time_offset.py       # declared vs submission + variance reasons
    pending.py           # manager pending work
  static/
    lockheed_weapons.json
    images/weapons/      # Tactical Library art
  templates/             # UI pages
load_live_roster.py      # apply roster to an existing database
INSTALL.md               # Windows / RHEL server install
```

Reference FOSC template (optional):  
`2025-Q3 In Country - Weekly Time Record Worksheet Validation Template.xlsx`

## License

MIT — see `LICENSE`.
