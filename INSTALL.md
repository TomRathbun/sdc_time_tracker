# Install SDC Time Tracker on a new computer

Use this when you stand up the kiosk/server PC at work. The app listens on **all interfaces, port 8888**. Staff open it in a browser on this machine or from another device on the LAN / Tailscale.

The live employee list is in git (`app/live_roster.py`). The SQLite database (`sdc_time.db`) is **not** in git — it is created on first start.

| | |
|---|---|
| Repo | `https://github.com/TomRathbun/sdc_time_tracker.git` |
| Python | 3.11 or newer |
| URL (this PC) | `http://127.0.0.1:8888` |
| URL (other PCs) | `http://<this-pc-ip>:8888` |
| First PIN | `1234` (everyone must change it on first login) |
| Managers | Jermaine Corley, Tom Rathbun |
| Supervisor | Omar Eldeeb |

Pick **Windows** or **RHEL** below, then do the **common** steps.

---

## Windows (step by step)

### 1. Install Git

1. Download Git for Windows: https://git-scm.com/download/win  
2. Install with the default options (Git from the command line is enough).

### 2. Install `uv` (includes Python)

Open **PowerShell** as a normal user (not required to be Administrator except for the firewall step later):

```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

Close and reopen PowerShell so `uv` is on your PATH. Check:

```powershell
uv --version
```

`uv` will download Python 3.11+ automatically when you sync the project. You do **not** have to install Python from python.org unless your site policy requires it.

### 3. Clone the repo

Choose a folder that stays on this PC (example: `C:\sdc_time_tracker`).

```powershell
cd C:\
git clone https://github.com/TomRathbun/sdc_time_tracker.git
cd sdc_time_tracker
```

If the repo is private, GitHub will ask you to sign in (browser or personal access token).

### 4. Install Python packages

```powershell
uv sync
```

### 5. Open the Windows Firewall (Administrator PowerShell)

Other devices cannot reach this PC until inbound TCP **8888** is allowed:

```powershell
netsh advfirewall firewall add rule name="SDC Time Tracker 8888" dir=in action=allow protocol=TCP localport=8888
```

### 6. Start the app

```powershell
cd C:\sdc_time_tracker
uv run python run.py --no-ssl
```

Leave this window open. First start creates `sdc_time.db` and seeds the live roster.

Open a browser on this PC: **http://127.0.0.1:8888**

From another PC on the same network, use this machine’s IPv4 address, for example **http://192.168.x.x:8888** (not `0.0.0.0`).

### 7. Start automatically at logon (optional)

1. Create `C:\sdc_time_tracker\start-sdc.cmd`:

```bat
@echo off
cd /d C:\sdc_time_tracker
uv run python run.py --no-ssl
```

2. Press **Win+R**, type `shell:startup`, press Enter.  
3. Put a shortcut to `start-sdc.cmd` in that folder.  
4. Log in as the kiosk Windows account at boot so the server starts.

Or use **Task Scheduler**: trigger **At log on**, action **Start a program** → `C:\sdc_time_tracker\start-sdc.cmd`, set “Start in” to `C:\sdc_time_tracker`.

---

## RHEL (step by step)

Tested against RHEL 8/9-style systems. You need `sudo` for packages, firewall, and systemd.

### 1. Install OS packages

```bash
sudo dnf install -y git gcc make tar
```

Python 3.11 (RHEL 9 AppStream; on RHEL 8 enable the module if `dnf` cannot find it):

```bash
sudo dnf install -y python3.11 python3.11-devel
python3.11 --version
```

### 2. Install `uv`

As the account that will run the app (not root):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
uv --version
```

Add that `source` line to `~/.bashrc` if a new shell does not find `uv`.

### 3. Clone the repo

Example location `/opt/sdc_time_tracker` owned by a dedicated user `sdc`:

```bash
sudo useradd -m -r -s /bin/bash sdc
sudo mkdir -p /opt/sdc_time_tracker
sudo chown sdc:sdc /opt/sdc_time_tracker
sudo -u sdc -H git clone https://github.com/TomRathbun/sdc_time_tracker.git /opt/sdc_time_tracker
```

If you prefer your own home directory:

```bash
cd ~
git clone https://github.com/TomRathbun/sdc_time_tracker.git
cd sdc_time_tracker
```

### 4. Install Python packages

```bash
cd /opt/sdc_time_tracker
sudo -u sdc -H bash -lc 'source $HOME/.local/bin/env && uv sync --python python3.11'
```

Or, if you cloned into your home directory:

```bash
cd ~/sdc_time_tracker
uv sync --python python3.11
```

### 5. Open the firewall

```bash
sudo firewall-cmd --permanent --add-port=8888/tcp
sudo firewall-cmd --reload
```

If this host also uses `iptables` only, allow TCP 8888 there instead.

SELinux: running from `/opt` or `$HOME` with the bundled SQLite file is normally fine. If the browser cannot load `/static/` after a custom layout, check `ausearch -m avc -ts recent`.

### 6. Test-start the app

```bash
cd /opt/sdc_time_tracker
sudo -u sdc -H bash -lc 'source $HOME/.local/bin/env && uv run python run.py --no-ssl'
```

Browse **http://127.0.0.1:8888**. Stop with Ctrl+C when it looks good.

### 7. systemd service (start on boot)

Create `/etc/systemd/system/sdc-time-tracker.service` (adjust paths and user if you did not use `/opt` and `sdc`):

```ini
[Unit]
Description=SDC Time Tracker
After=network.target

[Service]
Type=simple
User=sdc
Group=sdc
WorkingDirectory=/opt/sdc_time_tracker
Environment=PATH=/home/sdc/.local/bin:/usr/bin
ExecStart=/home/sdc/.local/bin/uv run python run.py --no-ssl
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sdc-time-tracker
sudo systemctl status sdc-time-tracker
```

Logs:

```bash
journalctl -u sdc-time-tracker -f
```

`run.py` currently uses the uvicorn **reloader**. That is fine for a dedicated kiosk box; if the service flaps, say so and we can switch it to a non-reload production start.

---

## Common steps (both OS)

### Confirm the roster

On a **brand-new** database, first start already creates all live employees.

If you copied an old `sdc_time.db` or still see demo names (John Smith, Admin Manager, …):

```bash
uv run python load_live_roster.py
```

That upserts the live list, deactivates leftovers, and **clears the audit log**.

Log in as **Tom Rathbun** or **Jermaine Corley** with PIN **1234**, then set a personal PIN.

### Shared kiosk browser

1. Use a dedicated Windows user or Linux account that auto-logs on.  
2. Set the browser homepage to `http://127.0.0.1:8888`.  
3. Turn **off** password saving (Opera/Chrome): **Settings → Privacy & security → Autofill → Passwords** → disable **Save passwords** and **Auto sign-in**.  
4. The app already tells the browser not to store PIN fields; the browser setting is the backup.

### Other PCs / phones

On the **server** machine, find the IPv4 address:

- Windows: `ipconfig` → Ethernet / Wi-Fi IPv4  
- RHEL: `ip -4 addr` or `hostname -I`

Browse `http://THAT_IP:8888` (HTTP, not HTTPS, when you used `--no-ssl`). Do not type `0.0.0.0` in the browser.

If it works on the server but not from another device, the firewall rule in the OS section was skipped.

### Tailscale (optional, off-LAN)

1. Install Tailscale on the server and on each remote client.  
2. On the server: `tailscale ip -4`  
3. Browse `http://<tailscale-ip>:8888`

### HTTPS (optional)

Only if you need TLS. From the project directory:

```bash
uv run python gen_cert.py
uv run python run.py
```

Then use `https://<ip>:8888` and accept the self-signed warning. For HTTP kiosk use, stay with `--no-ssl`.

### Updates later

```bash
cd <project-dir>
git pull
uv sync
```

Restart the process (Ctrl+C and start again, or `sudo systemctl restart sdc-time-tracker` on RHEL).

If `git pull` reports local changes you did not mean to keep, do not force-reset until you know you will not lose `sdc_time.db` (the database is gitignored, so it stays).

### Backup

Copy these files on a schedule (they hold all punches and PINs):

- `sdc_time.db`
- `sdc_time.db-wal` and `sdc_time.db-shm` if present
- `uploads/` (doctor notes, if used)

On Windows, a nightly copy to a share is enough. On RHEL, `cron` or a systemd timer.

### Reset audit log after testing

Manager login → **Administration → Data reset** → check **Clear audit log** → type `RESET`.

---

## Troubleshooting

| Symptom | What to do |
|---------|------------|
| Browser cannot open the page | Confirm the process is running. Use `http://127.0.0.1:8888`, not `0.0.0.0`. |
| Works locally, not from another PC | Add the firewall rule (Windows `netsh` / RHEL `firewall-cmd`). |
| Empty reply / SSL error | You started with `--no-ssl` but opened `https://`, or the reverse. Match the URL to the start command. |
| Unstyled page / giant clock | Hard-refresh (Ctrl+F5). Tailwind is local; do not need internet after `uv sync`. |
| Demo names on login | Run `uv run python load_live_roster.py`. |
| `uv: command not found` | Re-open the shell, or `source $HOME/.local/bin/env` (Linux). |
| Port already in use | Something else is on 8888. Stop it, or change the port in `run.py`. |
| Private clone fails | Sign in to GitHub; use a PAT or SSH key. |

---

## What success looks like

1. `http://127.0.0.1:8888` shows **SDC Time Tracker** with the live name list.  
2. Tom Rathbun or Jermaine Corley can log in with `1234` and is forced to set a new PIN.  
3. Another device on the LAN can open `http://<server-ip>:8888`.  
4. After reboot, the app is reachable again if you set Task Scheduler / systemd.
