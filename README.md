# SDC Time Tracker

Electronic Time and Attendance System (ETAS) for SDC staff on the FOSC (Follow-On Support Contract). Dark-themed FastAPI app with check-in/out, leave, manager approvals, TEMPO variance, and quarterly FOSC Excel export.

## Key features

### Timekeeping
- **Quick check-in / check-out** — one-tap entry from login or dashboard (office, remote, offsite)
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
- **Declared vs submission** — when declared time differs from device submission beyond a threshold, manager **offset approval** is required

### Manager / admin
- **Admin timesheet** — declared vs submitted, offsets, BEOD, leave, and future weeks
- **Leave approvals** — dedicated `/leave/approvals` page (separate from request UI)
- **Config** — BEOD, schedule, thresholds, and related settings
- **Audit trail** — change history and policy alerts
- **Production data reset** — clear demo/audit data before real staff go live

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

## Tech stack

| Layer | Choice |
|--------|--------|
| Backend | Python 3.11+, FastAPI |
| Database | SQLite + SQLAlchemy |
| Frontend | Jinja2, Tailwind CSS, HTMX |
| Excel export | openpyxl |
| Tooling | `uv` (recommended) |

## Installation

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

## Typical production flow

1. Reset demo data (admin) and create real employees  
2. Staff check in/out; log phone support, offsite, and leave as needed  
3. Managers approve leave, offsets, and review timesheets  
4. Import **TEMPO** weekly hours on **Reports**  
5. Export **FOSC weekly** or **quarterly** package for contract submission  
6. Open **Discrepancy Tracker** for base vs TEMPO shortfalls  

## Remote access (Tailscale)

1. Install Tailscale on host and client  
2. Host IP: `tailscale ip -4`  
3. Browse `http://[TAILSCALE-IP]:8888`  

## Project layout (high level)

```
app/
  models.py              # employees, entries, leave, TEMPO, summaries
  routes/                # auth, dashboard, time, leave, admin, reports
  services/
    fosc_export.py       # weekly/quarterly Excel + Discrepancy Tracker
    time_state.py        # check-in state machine
    leave_balance.py     # entitlements & pending
    leave_sync.py        # leave → DailySummary
    time_offset.py       # declared vs submission
    pending.py           # manager pending work
  static/
    lockheed_weapons.json
    images/weapons/      # Tactical Library art
  templates/             # UI pages
```

Reference FOSC template (optional):  
`2025-Q3 In Country - Weekly Time Record Worksheet Validation Template.xlsx`

## License

MIT — see `LICENSE`.
