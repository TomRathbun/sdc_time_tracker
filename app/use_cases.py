"""Demo catalog: real work scenarios mapped to the screens and taps that do them.

Used by the public /use-cases page so a demonstration can follow
scenario → action without hunting through menus.
"""

from __future__ import annotations

from typing import TypedDict


class Action(TypedDict, total=False):
    text: str
    href: str
    screen: str


class UseCase(TypedDict, total=False):
    id: str
    scenario: str
    who: str
    summary: str
    actions: list[Action]
    notes: str
    featured: bool


class UseCaseGroup(TypedDict):
    id: str
    title: str
    blurb: str
    cases: list[UseCase]


USE_CASE_GROUPS: list[UseCaseGroup] = [
    {
        "id": "punches",
        "title": "Daily punches",
        "blurb": "Kiosk list for on-time punches. Dashboard when you need to change the time or add a reason.",
        "cases": [
            {
                "id": "first-login",
                "scenario": "First login / change PIN",
                "who": "Everyone",
                "summary": "Select your name, enter PIN 1234, set a personal PIN.",
                "actions": [
                    {"text": "On the login list, tap your name", "href": "/login", "screen": "Login"},
                    {"text": "Enter the initial PIN (1234)"},
                    {"text": "Set a new 4-digit PIN when prompted", "href": "/reset-pin", "screen": "Reset PIN"},
                ],
                "notes": "Quick check-in/out arrows stay hidden until the PIN is changed.",
            },
            {
                "id": "on-time-in",
                "scenario": "On-time check-in",
                "who": "Employee",
                "summary": "Use Quick Check In.",
                "featured": True,
                "actions": [
                    {"text": "Find your name on the login list", "href": "/login", "screen": "Login"},
                    {"text": "Tap the green check-in icon (box, arrow going in)"},
                    {"text": "Enter PIN and confirm"},
                ],
                "notes": "Rounds down to the nearest 5 minutes. Projected checkout then shows next to your name (9h Mon–Thu, 8h with BEOD, 4h Friday).",
            },
            {
                "id": "late-in",
                "scenario": "Late check-in",
                "who": "Employee",
                "summary": "Log into the dashboard, use Check In, adjust time, add a reason.",
                "featured": True,
                "actions": [
                    {"text": "Tap your name and enter PIN", "href": "/login", "screen": "Login"},
                    {"text": "Open Check In", "href": "/time/checkin", "screen": "Check In"},
                    {"text": "Set the time you actually arrived"},
                    {"text": "Pick a canned reason (or Other) if the difference is over the threshold"},
                    {"text": "Submit"},
                ],
                "notes": "Declared time is what you worked; the device also stores the real submission time. Managers approve or reject the offset on the timesheet.",
            },
            {
                "id": "on-time-out",
                "scenario": "On-time check-out",
                "who": "Employee",
                "summary": "Use Quick Check Out.",
                "featured": True,
                "actions": [
                    {"text": "Find your name (shows In @ time)", "href": "/login", "screen": "Login"},
                    {"text": "Tap the check-out icon (box, arrow leaving)"},
                    {"text": "Enter PIN. Leave BEOD unchecked for a normal mid-day lunch"},
                    {"text": "Confirm"},
                ],
                "notes": "Rounds up to the nearest 5 minutes. Projected checkout clears after this punch (and at midnight).",
            },
            {
                "id": "beod-out",
                "scenario": "Leave early with BEOD",
                "who": "Employee",
                "summary": "Check Out and check Use BEOD (Mon–Thu, ≥6h worked).",
                "featured": True,
                "actions": [
                    {"text": "Quick Check Out, or Dashboard → Check Out", "href": "/time/checkout", "screen": "Check Out"},
                    {"text": "Check Use BEOD if it is not already your usual length, then pick 1 hour or 30 minutes for a half-hour lunch plus a half-hour BEOD"},
                    {"text": "Enter PIN if using the kiosk, then confirm"},
                ],
                "notes": "Example: 07:00–15:00 = 8h + 1h BEOD = 9h. A half-hour lunch plus a half-hour BEOD is 30 minutes of credit. The next checkout starts from that same choice. Hidden on Friday (4h day). If blanket BEOD is off, this is a request for the manager.",
            },
            {
                "id": "friday-out",
                "scenario": "Friday check-out",
                "who": "Employee",
                "summary": "Use Quick Check Out. BEOD is not offered.",
                "actions": [
                    {"text": "Tap the check-out icon (box, arrow leaving) on the login list", "href": "/login", "screen": "Login"},
                    {"text": "Enter PIN and confirm"},
                ],
                "notes": "Friday target is 4 hours. Quick checkout hides Use BEOD.",
            },
            {
                "id": "late-out",
                "scenario": "Late or early check-out (need a reason)",
                "who": "Employee",
                "summary": "Log into the dashboard, Check Out, adjust time, add a reason.",
                "actions": [
                    {"text": "Tap your name and enter PIN", "href": "/login", "screen": "Login"},
                    {"text": "Open Check Out", "href": "/time/checkout", "screen": "Check Out"},
                    {"text": "Set the time you actually left"},
                    {"text": "Pick a reason if the difference is over the threshold"},
                    {"text": "Submit"},
                ],
            },
            {
                "id": "nudge-time",
                "scenario": "Small time tweak (within the no-reason window)",
                "who": "Employee",
                "summary": "Use Quick Check In/Out and the − / + 5 minute buttons.",
                "actions": [
                    {"text": "Open Quick Check In or Check Out", "href": "/login", "screen": "Login"},
                    {"text": "Nudge recorded time with − / +"},
                    {"text": "Enter PIN and confirm"},
                ],
                "notes": "Stays inside the comment threshold (default 30 minutes). Larger gaps need the dashboard form and a reason.",
            },
        ],
    },
    {
        "id": "locations",
        "title": "SDC, remote, and offsite",
        "blurb": "Office is the default on quick punches. Use location on Check In/Out, or log a Remote site entry after you leave SDC.",
        "cases": [
            {
                "id": "sdc-day",
                "scenario": "All day at SDC (office)",
                "who": "Employee",
                "summary": "Quick Check In in the morning, Quick Check Out when you leave.",
                "actions": [
                    {"text": "Quick Check In", "href": "/login", "screen": "Login"},
                    {"text": "Quick Check Out at end of day"},
                ],
                "notes": "Quick punches always record location as Office.",
            },
            {
                "id": "sdc-to-remote-eod",
                "scenario": "SDC to Remote Site EOD",
                "who": "Employee",
                "summary": "Tap the offsite icon. If you are checked in, that checks you out and starts the site visit.",
                "featured": True,
                "actions": [
                    {"text": "Check In at SDC (Quick Check In, or Dashboard Check In with Office)", "href": "/time/checkin", "screen": "Check In"},
                    {"text": "Tap the offsite icon. Confirm the location. Start time is your checkout.", "href": "/login", "screen": "Login"},
                    {"text": "Check in when you get back. That sets the offsite end time.", "href": "/login", "screen": "Login"},
                ],
                "notes": "Leave the end open unless you already know it. Offsite hours add to FOSC once the visit is closed. A gap with no open offsite still offers the Remote Site Work form.",
            },
            {
                "id": "midday-remote-gap",
                "scenario": "Leave SDC, work a remote site, come back",
                "who": "Employee",
                "summary": "Check Out, later Check In — then confirm the Remote Site Work prompt.",
                "actions": [
                    {"text": "Check Out when leaving SDC", "href": "/time/checkout", "screen": "Check Out"},
                    {"text": "Check In (Return) when you come back", "href": "/time/checkin", "screen": "Check In"},
                    {"text": "If the gap is ≥15 minutes, enter where you were on Remote Site Work", "href": "/time/offsite-gap", "screen": "Remote Site Work"},
                ],
                "notes": "A real gap (doctor, client site) stays as two In/Out pairs on the timesheet. Continuous re-checkout with no gap squashes to first in / last out.",
            },
            {
                "id": "all-day-remote",
                "scenario": "All-day remote / WFH",
                "who": "Employee",
                "summary": "Log into the dashboard, Check In, set Location to Remote.",
                "actions": [
                    {"text": "Tap your name and enter PIN", "href": "/login", "screen": "Login"},
                    {"text": "Open Check In", "href": "/time/checkin", "screen": "Check In"},
                    {"text": "Choose Location: Remote"},
                    {"text": "Check Out later with Location: Remote", "href": "/time/checkout", "screen": "Check Out"},
                ],
                "notes": "Managers can pre-authorize remote days under Admin → Remote Work Authorization.",
            },
            {
                "id": "planned-offsite",
                "scenario": "Planned offsite (client site, Al-Dhafra, training)",
                "who": "Employee",
                "summary": "Dashboard → Offsite → location and times.",
                "actions": [
                    {"text": "Open Offsite from the dashboard", "href": "/time/offsite", "screen": "Offsite"},
                    {"text": "Enter location, start, and end"},
                    {"text": "Submit"},
                ],
            },
            {
                "id": "phone-support",
                "scenario": "Evening/weekend phone call",
                "who": "Employee",
                "summary": "Tap the phone icon on the roster for today's call. Sign in and use Phone if the call was on another day.",
                "featured": True,
                "actions": [
                    {"text": "Tap the phone icon, pick the length, add a note, enter PIN", "href": "/login", "screen": "Login"},
                    {"text": "Or sign in and open Phone to log a past day", "href": "/time/phone-support", "screen": "Phone"},
                ],
                "notes": "Do not check in or re-check out for a phone call from home. Phone hours add to FOSC for that date. The roster icon is today only. Phone stays on the dashboard on weekends.",
            },
        ],
    },
    {
        "id": "corrections",
        "title": "Called back, forgotten punches, extra time",
        "blurb": "Re-check out for a short call-back. Past Day for yesterday. Extra session if the call-back was on a prior day.",
        "cases": [
            {
                "id": "recheckout",
                "scenario": "Boss called you back after check-out",
                "who": "Employee",
                "summary": "Use Re-Check Out (or Return, then Check Out).",
                "featured": True,
                "actions": [
                    {"text": "On the login list, tap the check-out icon again (box, arrow leaving)", "href": "/login", "screen": "Login"},
                    {"text": "Enter PIN — extra time is from last checkout until now"},
                    {"text": "Or tap Return (green) if you are still working, then Check Out when you leave"},
                ],
                "notes": "Creates a second session. If it is continuous, the manager timesheet shows first in / last out. For a phone call from home, use Phone instead.",
            },
            {
                "id": "past-day",
                "scenario": "Forgot to punch yesterday",
                "who": "Employee",
                "summary": "Dashboard → Past Day → date, in/out, comment.",
                "featured": True,
                "actions": [
                    {"text": "Open Past Day", "href": "/time/past-day", "screen": "Past Day"},
                    {"text": "Pick the date (lookback is 14 days)"},
                    {"text": "Enter check-in, check-out, lunch/offsite if needed, and a comment"},
                    {"text": "Submit — managers get an audit alert"},
                ],
            },
            {
                "id": "past-extra",
                "scenario": "Extra session on a past day",
                "who": "Employee",
                "summary": "Past Day → Add extra session.",
                "actions": [
                    {"text": "Open Past Day and pick the date", "href": "/time/past-day", "screen": "Past Day"},
                    {"text": "Use Add extra session (does not wipe the rest of the day)"},
                    {"text": "Enter the extra in/out and a comment"},
                ],
            },
        ],
    },
    {
        "id": "leave",
        "title": "Leave and PTO",
        "blurb": "Full days on Leave. A few hours on Partial Leave. Vacation 22 days (176h), sick 15 days (120h).",
        "cases": [
            {
                "id": "partial-leave",
                "scenario": "Partial leave (doctor, few hours)",
                "who": "Employee",
                "summary": "Dashboard → Partial Leave → hours and type.",
                "featured": True,
                "actions": [
                    {"text": "Open Partial Leave", "href": "/time/partial-leave", "screen": "Partial Leave"},
                    {"text": "Pick the date, hours, and vacation or sick"},
                    {"text": "Submit for manager approval"},
                ],
                "notes": "Charges the hours you enter against the hour bank. Worked hours + PTO should meet the day’s target.",
            },
            {
                "id": "vacation",
                "scenario": "Full-day vacation",
                "who": "Employee",
                "summary": "Leave → Request Vacation.",
                "actions": [
                    {"text": "Open Leave", "href": "/leave", "screen": "Leave"},
                    {"text": "Set start and end dates under Request Vacation"},
                    {"text": "Submit — manager approves on Approvals"},
                ],
                "notes": "Each workday charges that day’s target (9h Mon–Thu, 4h Friday).",
            },
            {
                "id": "sick",
                "scenario": "Sick day",
                "who": "Employee",
                "summary": "Leave → Record Sick Leave.",
                "actions": [
                    {"text": "Open Leave", "href": "/leave", "screen": "Leave"},
                    {"text": "Set dates under Record Sick Leave"},
                    {"text": "Submit for approval"},
                ],
            },
            {
                "id": "covid",
                "scenario": "COVID sick",
                "who": "Employee",
                "summary": "Leave → COVID Sick Hours.",
                "actions": [
                    {"text": "Open Leave", "href": "/leave", "screen": "Leave"},
                    {"text": "Submit COVID Sick with dates"},
                ],
                "notes": "Uses the same sick hour bank.",
            },
            {
                "id": "uae-holiday",
                "scenario": "UAE national holiday",
                "who": "Employee",
                "summary": "Leave → UAE National Holiday.",
                "actions": [
                    {"text": "Open Leave", "href": "/leave", "screen": "Leave"},
                    {"text": "Submit the holiday dates (name in comments)"},
                ],
                "notes": "Does not use vacation or sick balance. Full-day FOSC credit after approval.",
            },
        ],
    },
    {
        "id": "manager",
        "title": "Manager and supervisor",
        "blurb": "Timesheet for punches, offsets, and BEOD. Approvals for leave. Reports for FOSC and TEMPO.",
        "cases": [
            {
                "id": "approve-leave",
                "scenario": "Approve or reject leave",
                "who": "Manager / Supervisor",
                "summary": "Nav → Approvals.",
                "actions": [
                    {"text": "Open Approvals", "href": "/leave/approvals", "screen": "Approvals"},
                    {"text": "Approve or reject each pending request"},
                ],
            },
            {
                "id": "timesheet-offset",
                "scenario": "Approve or reject a time variance",
                "who": "Manager / Supervisor",
                "summary": "Timesheet → hover the reason → Approve or Reject.",
                "featured": True,
                "actions": [
                    {"text": "Open Team Timesheet", "href": "/admin/timesheet", "screen": "Timesheet"},
                    {"text": "Hover the offset to read the employee’s reason"},
                    {"text": "Approve keeps declared time; Reject reverts to the actual punch time"},
                ],
            },
            {
                "id": "timesheet-beod",
                "scenario": "Approve BEOD or PTO on the timesheet",
                "who": "Manager / Supervisor",
                "summary": "Timesheet checkboxes for pending BEOD and PTO.",
                "actions": [
                    {"text": "Open Team Timesheet", "href": "/admin/timesheet", "screen": "Timesheet"},
                    {"text": "Approve pending BEOD or PTO on the day cell"},
                ],
                "notes": "Continuous re-checkout sessions squash to first in / last out. A real gap stays as separate punches.",
            },
            {
                "id": "remote-auth",
                "scenario": "Authorize remote work",
                "who": "Manager",
                "summary": "Admin → Remote Work Authorization.",
                "actions": [
                    {"text": "Open Admin", "href": "/admin", "screen": "Admin"},
                    {"text": "Choose employee, dates, hours, and location"},
                    {"text": "Save the authorization"},
                ],
            },
            {
                "id": "roster",
                "scenario": "Add employee or reset a PIN",
                "who": "Manager",
                "summary": "Admin → Add Employee / edit / reset PIN.",
                "actions": [
                    {"text": "Open Admin", "href": "/admin", "screen": "Admin"},
                    {"text": "Add a person, change role, or trigger a PIN reset"},
                ],
            },
            {
                "id": "reports-fosc",
                "scenario": "TEMPO import and FOSC export",
                "who": "Manager / Supervisor",
                "summary": "Reports → import TEMPO, then export weekly or quarterly.",
                "actions": [
                    {"text": "Open Reports", "href": "/reports", "screen": "Reports"},
                    {"text": "Import weekly TEMPO hours", "href": "/reports/tempo", "screen": "TEMPO"},
                    {"text": "Export FOSC weekly or quarterly workbook"},
                ],
            },
            {
                "id": "vacation-print",
                "scenario": "Print projected vacation schedule",
                "who": "Manager / Supervisor",
                "summary": "Reports → Vacation schedule.",
                "actions": [
                    {"text": "Open the vacation schedule", "href": "/reports/vacation-schedule", "screen": "Vacation schedule"},
                    {"text": "Print or export Excel for the customer"},
                ],
            },
            {
                "id": "audit",
                "scenario": "Review the audit trail",
                "who": "Manager / Supervisor",
                "summary": "Nav → Audit.",
                "actions": [
                    {"text": "Open Audit", "href": "/audit", "screen": "Audit"},
                    {"text": "Filter by person and date if needed"},
                ],
            },
            {
                "id": "config",
                "scenario": "Change variance reasons, BEOD, or thresholds",
                "who": "Manager",
                "summary": "Admin → Feature Config.",
                "actions": [
                    {"text": "Open Feature Config", "href": "/admin/config", "screen": "Config"},
                    {"text": "Edit canned variance reasons, comment threshold, BEOD, emails"},
                    {"text": "Save"},
                ],
            },
        ],
    },
]


def featured_cases() -> list[UseCase]:
    """Cases marked for the demo glance table, in group order."""
    out: list[UseCase] = []
    for group in USE_CASE_GROUPS:
        for case in group["cases"]:
            if case.get("featured"):
                out.append(case)
    return out
