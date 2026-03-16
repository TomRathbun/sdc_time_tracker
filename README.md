# SDC Time Tracker

A modern, high-performance Electronic Time and Attendance System (ETAS) designed for SDC employees. Features a premium, dark-themed interface with real-time status tracking and compliance monitoring.

![Aesthetic Dashboard Preview](https://github.com/TomRathbun/sdc_time_tracker/raw/main/app/static/img/dashboard_preview.png) *(Placeholder if you add images later)*

## ✨ Key Features

- **Quick Check-In/Out**: One-tap entry with location tracking (Office, Remote, Offsite).
- **Past Day Entry**: Log full timelines for previous workdays (up to 14 days back).
- **Remote Site Work**: Dedicated flow for logging offsite client visits and gap detection.
- **PTO & Leave Management**: Request partial or full-day leave with manager approval flow.
- **Compliance Monitoring**: Real-time progress tracking against daily/weekly targets (e.g., 9h Mon-Thu, 4h Fri).
- **Admin Dashboard**: Comprehensive employee management, timesheet approvals, and audit trails.
- **Security First**: 
  - PIN-based authentication.
  - Forced PIN reset for first-time users.
  - Signed session tokens.

## 🚀 Tech Stack

- **Backend**: Python 3.11+, FastAPI
- **Database**: SQLite (SQLAlchemy ORM)
- **Frontend**: Jinja2 Templates, Tailwind CSS, HTMX
- **Environment**: Managed with `uv`

## 🛠️ Installation & Setup

1. **Copy the repository**:
   ```bash
   cd sdc_time_tracker
   ```

2. **Install dependencies**:
   Using `uv` (recommended):
   ```bash
   uv sync
   ```
   Or using pip:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the server**:
   ```bash
   uv run python run.py --no-ssl
   ```
   The application will be available at [http://localhost:8888](http://localhost:8888).

## 🌍 Remote Access (Tailscale)

This system is configured to accept connections over Tailscale. 
1. Install Tailscale on your host and remote device.
2. Find your host's Tailscale IP: `tailscale ip -4`.
3. Access via `http://[TAILSCALE-IP]:8888`.

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.
