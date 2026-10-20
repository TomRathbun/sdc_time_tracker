"""Export team time into FOSC weekly / quarterly attendance spreadsheet format.

The official contract process is quarterly; each quarter is a set of weekly
attendance sheets (Mon–Sun columns, In/Out/Phone/Offsite/FOSC/leave rows).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.orm import Session

from app.services.time_calc import get_target_hours
from app.config import BASE_DIR
from app.models import (
    Employee, TimeEntry, OffsiteEntry, PhoneSupportEntry, DailySummary,
    EntryType, LeaveType, LeaveRequest, LeaveStatus, Role, TempoWeekly,
)

# Reference template (user may slim sheets; we generate a lean workbook)
TEMPLATE_FILENAME = (
    "2025-Q3 In Country - Weekly Time Record Worksheet Validation Template.xlsx"
)


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _excel_time(dt: datetime | None):
    if not dt:
        return None
    t = dt.time()
    return (t.hour * 3600 + t.minute * 60 + t.second) / 86400.0


def quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    """Return (first day of quarter, last day of quarter)."""
    if quarter not in (1, 2, 3, 4):
        raise ValueError("quarter must be 1–4")
    start_month = (quarter - 1) * 3 + 1
    start = date(year, start_month, 1)
    if quarter == 4:
        end = date(year, 12, 31)
    else:
        end = date(year, start_month + 3, 1) - timedelta(days=1)
    return start, end


def mondays_in_quarter(year: int, quarter: int) -> list[date]:
    """Every Monday that falls inside the quarter (weeks that touch the quarter)."""
    q_start, q_end = quarter_bounds(year, quarter)
    # Start from Monday of week containing first day
    m = _monday(q_start)
    mondays = []
    while m <= q_end:
        # Include week if any day Mon–Sun falls in quarter
        week_end = m + timedelta(days=6)
        if week_end >= q_start and m <= q_end:
            mondays.append(m)
        m += timedelta(days=7)
    return mondays


def _day_data(db: Session, emp_id: int, d: date) -> dict:
    entries = (
        db.query(TimeEntry)
        .filter(TimeEntry.employee_id == emp_id, TimeEntry.date == d)
        .order_by(TimeEntry.declared_time)
        .all()
    )
    cis = [e for e in entries if e.entry_type == EntryType.check_in]
    cos = [e for e in entries if e.entry_type == EntryType.check_out]
    first_in = min((e.declared_time for e in cis), default=None)
    last_out = max((e.declared_time for e in cos), default=None)

    phones = (
        db.query(PhoneSupportEntry)
        .filter(PhoneSupportEntry.employee_id == emp_id, PhoneSupportEntry.date == d)
        .all()
    )
    phone_h = round(sum(p.hours or 0 for p in phones), 2) or None

    offsites = (
        db.query(OffsiteEntry)
        .filter(OffsiteEntry.employee_id == emp_id, OffsiteEntry.date == d)
        .all()
    )
    off_h = 0.0
    for o in offsites:
        if o.end_time and o.start_time and o.end_time > o.start_time:
            off_h += (o.end_time - o.start_time).total_seconds() / 3600.0
    off_h = round(off_h, 2) or None

    summary = (
        db.query(DailySummary)
        .filter(DailySummary.employee_id == emp_id, DailySummary.date == d)
        .first()
    )

    vac = sick = covid = hol = None
    work = None
    beod = None
    if d.weekday() < 5:
        beod = 0.0
        work = 0.0
    if summary:
        clock = float(summary.clock_hours or 0) + float(summary.offsite_hours or 0) + float(summary.phone_hours or 0)
        claimed = 0.0
        if summary.lunch_end_of_day or summary.beod_hours:
            claimed = float(summary.beod_hours or summary.beod_requested_hours or 1.0)
        if clock == 0 and summary.total_hours:
            clock = max(0.0, float(summary.total_hours) - float(summary.beod_hours or 0))
        if d.weekday() < 5:
            work = round(clock, 2)
            beod = round(claimed, 2)

    lt = None
    val = 0.0
    if summary and summary.leave_hours:
        lt = summary.leave_type
        val = float(summary.leave_hours)
    if val <= 0 and d.weekday() < 5:
        pending = (
            db.query(LeaveRequest)
            .filter(
                LeaveRequest.employee_id == emp_id,
                LeaveRequest.status.in_([LeaveStatus.pending, LeaveStatus.approved]),
                LeaveRequest.start_date <= d,
                LeaveRequest.end_date >= d,
            )
            .first()
        )
        if pending:
            lt = pending.leave_type
            val = float(get_target_hours(d))
    if val > 0:
        if isinstance(lt, str):
            try:
                lt = LeaveType(lt)
            except ValueError:
                lt = None
        if lt == LeaveType.vacation:
            vac = val
        elif lt == LeaveType.sick:
            sick = val
        elif lt == LeaveType.covid_sick:
            covid = val
        elif lt == LeaveType.uae_holiday:
            hol = val

    return {
        "in": first_in,
        "out": last_out,
        "phone": phone_h,
        "offsite": off_h,
        "fosc": work,
        "beod": beod,
        "vacation": vac,
        "sick": sick,
        "covid": covid,
        "holiday": hol,
    }


def _active_employees(db: Session) -> list[Employee]:
    employees = (
        db.query(Employee)
        .filter(
            Employee.is_active == True,  # noqa: E712
            Employee.role != Role.customer,
        )
        .all()
    )
    return sorted(
        employees,
        key=lambda e: (0 if e.role == Role.employee else 1, e.name.lower()),
    )


def _style_constants():
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF", size=12)
    name_font = Font(bold=True, size=11)
    cat_font = Font(size=10)
    thin = Border(
        left=Side(style="thin", color="CCCCCC"),
        right=Side(style="thin", color="CCCCCC"),
        top=Side(style="thin", color="CCCCCC"),
        bottom=Side(style="thin", color="CCCCCC"),
    )
    return header_fill, header_font, name_font, cat_font, thin


def _tempo_hours(db: Session, emp_id: int, week_start: date) -> float | None:
    row = (
        db.query(TempoWeekly)
        .filter(
            TempoWeekly.employee_id == emp_id,
            TempoWeekly.week_start == week_start,
        )
        .first()
    )
    return float(row.hours) if row else None


def write_week_sheet(
    ws: Worksheet,
    db: Session,
    week_start: date,
    employees: list[Employee],
    *,
    title_suffix: str = "",
) -> list[dict]:
    """One customer attendance sheet for a week. No TEMPO, signature, or remarks."""
    week_start = _monday(week_start)
    days = [week_start + timedelta(days=i) for i in range(7)]
    header_fill, header_font, name_font, cat_font, thin = _style_constants()
    orange = PatternFill("solid", fgColor="F8CBAD")
    green = PatternFill("solid", fgColor="C6EFCE")
    gray = PatternFill("solid", fgColor="D9D9D9")
    peach = PatternFill("solid", fgColor="F4B183")
    pto_fill = PatternFill("solid", fgColor="FFF2CC")
    beod_fill = PatternFill("solid", fgColor="DDEBF7")
    name_fill = PatternFill("solid", fgColor="D6DCE4")
    center = Alignment(horizontal="center", vertical="center")

    title = "EADGE-T FOLLOW ON SERVICE CONTRACT - Weekly ATTENDANCE SHEET"
    if title_suffix:
        title = f"{title} ({title_suffix})"
    ws.merge_cells("A1:J1")
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=14, color="1F4E79")
    ws["A2"] = "Week Starting"
    ws["B2"] = datetime.combine(week_start, time.min)
    ws["B2"].number_format = "M/D/YYYY"
    ws.merge_cells("D2:J2")
    ws["D2"] = "CONTRACT NO.: DP3/4/N/1/2021/7 : Purchase Order"
    ws["D2"].font = Font(size=10, color="1F4E79")

    headers = ["Name", "", "M", "T", "W", "T", "F", "S", "S", "Total Hours"]
    for col, label in enumerate(headers, 1):
        cell = ws.cell(4, col, label)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center
    for i, d in enumerate(days):
        cell = ws.cell(5, 3 + i, d)
        cell.number_format = "DD-MMM"
        cell.alignment = center
        cell.font = Font(size=9, italic=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2E75B6")
        if i == 4:
            cell.fill = PatternFill("solid", fgColor="548235")
        elif i >= 5:
            cell.fill = PatternFill("solid", fgColor="7F7F7F")

    rows = [
        ("In", "in", "time"),
        ("Out", "out", "time"),
        ("BEOD", "beod", "num"),
        ("Normal Time - FOSC", "fosc", "num"),
        ("Vacation", "vacation", "num"),
        ("Sick Hours", "sick", "num"),
        ("COVID Sick Hours", "covid", "num"),
        ("UAE National Holiday", "holiday", "num"),
    ]
    row = 6
    meta: list[dict] = []
    for emp in employees:
        by_day = {d: _day_data(db, emp.id, d) for d in days}
        ws.merge_cells(start_row=row, start_column=1, end_row=row + len(rows) + 1, end_column=1)
        name_cell = ws.cell(row, 1, emp.name)
        name_cell.font = name_font
        name_cell.fill = name_fill
        name_cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

        fosc_row = beod_row = None
        leave_first = leave_last = None
        for label, key, kind in rows:
            ws.cell(row, 2, label).font = Font(bold=True, size=10) if key in ("fosc", "beod") else cat_font
            if key == "fosc":
                fosc_row = row
            if key == "beod":
                beod_row = row
            if key == "vacation":
                leave_first = row
            if key == "holiday":
                leave_last = row
            for i, d in enumerate(days):
                cell = ws.cell(row, 3 + i)
                cell.border = thin
                cell.alignment = center
                weekend = i >= 5
                if weekend:
                    cell.value = "—"
                    cell.fill = gray
                    continue
                value = by_day[d][key]
                if kind == "time":
                    cell.value = _excel_time(value)
                    if cell.value is not None:
                        cell.number_format = "HH:MM"
                else:
                    cell.value = 0 if value is None and key in ("beod", "fosc") else value
                    if cell.value is not None:
                        cell.number_format = "0.0"
                if key == "fosc":
                    cell.fill = green if i == 4 else orange
                elif key == "beod" and value:
                    cell.fill = beod_fill
                elif key == "holiday" and value:
                    cell.fill = orange
            total = ws.cell(row, 10, f"=SUM(C{row}:G{row})")
            total.number_format = "0.0"
            total.font = Font(bold=True)
            total.border = thin
            total.alignment = center
            row += 1

        ws.cell(row, 2, "PTO Total").font = Font(bold=True, size=10)
        pto_row = row
        for i in range(7):
            col = get_column_letter(3 + i)
            cell = ws.cell(row, 3 + i, f"=SUM({col}{leave_first}:{col}{leave_last})")
            cell.number_format = "0.0"
            cell.font = Font(bold=True)
            cell.fill = gray if i >= 5 else pto_fill
            cell.border = thin
            cell.alignment = center
        pto_total = ws.cell(row, 10, f"=SUM(C{row}:G{row})")
        pto_total.number_format = "0.0"
        pto_total.font = Font(bold=True)
        pto_total.fill = pto_fill
        pto_total.border = thin
        row += 1

        ws.cell(row, 2, "Overall Total").font = Font(bold=True, size=10)
        for i in range(7):
            col = get_column_letter(3 + i)
            cell = ws.cell(
                row, 3 + i,
                f"={col}{fosc_row}+{col}{beod_row}+{col}{pto_row}",
            )
            cell.number_format = "0.0"
            cell.font = Font(bold=True)
            cell.fill = gray if i >= 5 else peach
            cell.border = thin
            cell.alignment = center
        overall = ws.cell(row, 10, f"=SUM(C{row}:G{row})")
        overall.number_format = "0.0"
        overall.font = Font(bold=True, color="FFFFFF")
        overall.fill = header_fill
        overall.border = thin
        meta.append({
            "employee_id": emp.id,
            "name": emp.name,
            "role": emp.role,
            "fosc_row": fosc_row,
            "beod_row": beod_row,
            "pto_row": pto_row,
            "week_start": week_start,
        })
        row += 2

    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 26
    for col in range(3, 11):
        ws.column_dimensions[get_column_letter(col)].width = 12
    ws.row_dimensions[1].height = 22
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.freeze_panes = "C6"
    ws.oddHeader.left.text = title
    return meta


def _add_notes_sheet(wb: Workbook, lines: list[str]) -> None:
    leg = wb.create_sheet("Export Notes", 0)
    leg["A1"] = "FOSC Export — SDC Time Tracker"
    leg["A1"].font = Font(bold=True, size=12)
    for i, line in enumerate(lines, 2):
        leg[f"A{i}"] = line
    leg.column_dimensions["A"].width = 95


def _sheet_ref(title: str) -> str:
    """Excel sheet reference with quoting when needed."""
    if any(ch in title for ch in (" ", "'", "-", "!")):
        safe = title.replace("'", "''")
        return f"'{safe}'"
    return title


def write_discrepancy_sheet(
    ws: Worksheet,
    week_metas: list[tuple[str, date, list[dict]]],
    *,
    title: str = "Hours Discrepancies",
) -> None:
    """
    Discrepancy Tracker matching the contract workbook screenshot:

      A1  Hours Discrepancies
      A2  Note: only base hours less than TEMPO (obligation reductions)
      A3  No Data (status chip)
      B5… Wk1 … Wk14
      A6… employee names
      B6… INDEX/MATCH into each week sheet col M (Hours to Reduce)

    Exact formula pattern from their sheet (B6 example):
      =INDEX('Time Keeping Sheet (Wk1)'!$A$6:$M$999,
             MATCH('Discrepancy Tracker'!$A6,
                   'Time Keeping Sheet (Wk1)'!$A$6:$A$999,FALSE)+offset,13)

    offset = rows from employee name → Hours from TEMPO on that week sheet
    (11 with our full block: In/Out/Phone/Offsite/FOSC/4 leave/Totals/TEMPO).

    Col M on the week sheet:
      =IF(SUM(Jtotals-Jtempo)>0,0,SUM(Jtotals-Jtempo))
    """
    thin = Border(
        left=Side(style="thin", color="CCCCCC"),
        right=Side(style="thin", color="CCCCCC"),
        top=Side(style="thin", color="CCCCCC"),
        bottom=Side(style="thin", color="CCCCCC"),
    )
    peach = PatternFill("solid", fgColor="F8CBAD")
    total_fill = PatternFill("solid", fgColor="D9E2F3")
    num_format = "0.0"
    # Always show Wk1–Wk14 columns like the template, even if fewer weeks exist
    n_display = 14

    # ── Header block (screenshot layout) ─────────────────────────────
    ws["A1"] = title or "Hours Discrepancies"
    ws["A1"].font = Font(bold=True, size=16, color="000000")

    ws.merge_cells("A2:O2")
    ws["A2"] = (
        "Note: Only hours on base that are less than TEMPO hours are listed below, "
        "and are potential reductions to Quarterly Obligation Sheet. "
        "Any hours on base above TEMPO hours are not included."
    )
    ws["A2"].font = Font(size=9, italic=True, color="666666")

    ws["A3"] = "No Data"
    ws["A3"].fill = peach
    ws["A3"].font = Font(size=10, color="833C0C")

    # Row 5: Wk1 … Wk14
    for wi in range(n_display):
        cell = ws.cell(5, 2 + wi, f"Wk{wi + 1}")
        cell.font = Font(bold=True, size=10)
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin

    # Optional week-start dates on row 4 (not in screenshot; keep subtle for ops)
    for wi in range(min(n_display, len(week_metas))):
        _, week_start, _ = week_metas[wi]
        cell = ws.cell(4, 2 + wi, week_start)
        cell.number_format = "DD-MMM"
        cell.font = Font(size=8, color="888888")
        cell.alignment = Alignment(horizontal="center")

    employees_meta = week_metas[0][2] if week_metas else []
    default_offset = 11
    if employees_meta:
        default_offset = int(employees_meta[0].get("name_to_tempo_offset", 11))

    first_data_row = 6
    disc_ref = _sheet_ref(ws.title)  # usually 'Discrepancy Tracker'

    for ei, m0 in enumerate(employees_meta):
        r = first_data_row + ei
        ws.cell(r, 1, m0["name"]).font = Font(size=10)
        ws.cell(r, 1).border = thin

        for wi in range(n_display):
            cell = ws.cell(r, 2 + wi)
            cell.border = thin
            cell.number_format = num_format
            cell.alignment = Alignment(horizontal="center")

            if wi >= len(week_metas):
                # No week sheet for this column (e.g. short quarter / weekly export)
                cell.value = "-"
                continue

            sheet_title, _week_start, meta = week_metas[wi]
            offset = default_offset
            for m in meta:
                if m["name"] == m0["name"]:
                    offset = int(m.get("name_to_tempo_offset", default_offset))
                    break

            ref = _sheet_ref(sheet_title)
            # Match contract formula (screenshot formula bar):
            # INDEX(week!$A$6:$M$999, MATCH(disc!$A6, week!$A$6:$A$999, FALSE)+offset, 13)
            # IFERROR → "-" when name not found (same as #REF!/blank weeks in template)
            formula = (
                f"=IFERROR("
                f"INDEX({ref}!$A$6:$M$999,"
                f"MATCH({disc_ref}!$A{r},{ref}!$A$6:$A$999,FALSE)+{offset},13)"
                f",\"-\")"
            )
            cell.value = formula

    last_emp_row = (
        first_data_row + len(employees_meta) - 1
        if employees_meta
        else first_data_row
    )

    # Discrepancy Total (template row 34 style: =SUM(B6:B…))
    total_row = last_emp_row + 1 if employees_meta else first_data_row + 1
    ws.cell(total_row, 1, "Discrepancy Total").font = Font(bold=True, size=10)
    for wi in range(n_display):
        col = get_column_letter(2 + wi)
        cell = ws.cell(total_row, 2 + wi)
        if employees_meta and wi < len(week_metas):
            cell.value = f"=SUM({col}{first_data_row}:{col}{last_emp_row})"
        elif employees_meta:
            cell.value = 0
        else:
            cell.value = 0
        cell.font = Font(bold=True)
        cell.number_format = num_format
        cell.border = thin
        cell.fill = total_fill

    # Brief legend (below totals)
    note_row = total_row + 2
    ws.cell(note_row, 1, "How to read this sheet").font = Font(bold=True, size=11)
    ws.cell(note_row + 1, 1, (
        "Each cell uses INDEX/MATCH into that week’s Time Keeping sheet, column M "
        "(Hours to Reduce on the Hours from TEMPO row). "
        "Week formula: IF(SDC−TEMPO>0, 0, SDC−TEMPO) — only shortfalls (base < TEMPO). "
        "Import TEMPO weekly hours on Reports before exporting so column M is populated."
    ))
    ws.merge_cells(start_row=note_row + 1, start_column=1, end_row=note_row + 2, end_column=10)
    ws.cell(note_row + 1, 1).font = Font(size=9, color="555555")
    ws.cell(note_row + 1, 1).alignment = Alignment(wrap_text=True, vertical="top")

    ws.column_dimensions["A"].width = 28
    for wi in range(n_display):
        ws.column_dimensions[get_column_letter(2 + wi)].width = 9


def _week_sheet_title(week_index: int) -> str:
    """Contract-style tab name: Time Keeping Sheet (Wk1) — max 31 chars."""
    return f"Time Keeping Sheet (Wk{week_index})"[:31]


def build_fosc_weekly_workbook(db: Session, week_start: date) -> BytesIO:
    """Single-week customer attendance sheet."""
    week_start = _monday(week_start)
    employees = _active_employees(db)
    wb = Workbook()
    ws = wb.active
    ws.title = f"Week {week_start.strftime('%d %b')}"[:31]
    write_week_sheet(ws, db, week_start, employees)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def build_fosc_weeks_workbook(db: Session, mondays: list[date]) -> BytesIO:
    """One attendance sheet per week, in the customer format."""
    mondays = [_monday(day) for day in mondays]
    employees = _active_employees(db)
    wb = Workbook()
    default = wb.active
    for idx, mon in enumerate(mondays, 1):
        title = f"Week {mon.strftime('%d %b')}"[:31]
        ws = default if idx == 1 else wb.create_sheet(title)
        ws.title = title
        write_week_sheet(ws, db, mon, employees, title_suffix=f"Wk{idx}")
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def build_fosc_quarterly_workbook(db: Session, year: int, quarter: int) -> BytesIO:
    """Quarter totals: FOSC, BEOD, and PTO for each employee and manager."""
    q_start, q_end = quarter_bounds(year, quarter)
    employees = _active_employees(db)
    wb = Workbook()
    ws = wb.active
    ws.title = f"Q{quarter} {year}"
    header_fill, header_font, name_font, _, thin = _style_constants()
    center = Alignment(horizontal="center", vertical="center")
    peach = PatternFill("solid", fgColor="F4B183")
    pto_fill = PatternFill("solid", fgColor="FFF2CC")
    orange = PatternFill("solid", fgColor="F8CBAD")

    ws.merge_cells("A1:F1")
    ws["A1"] = "EADGE-T FOLLOW ON SERVICE CONTRACT - QUARTERLY TOTALS"
    ws["A1"].font = Font(bold=True, size=14, color="1F4E79")
    ws.merge_cells("A2:F2")
    ws["A2"] = (
        f"Q{quarter} {year}  ({q_start.strftime('%d %b %Y')} – {q_end.strftime('%d %b %Y')})"
        "    CONTRACT NO.: DP3/4/N/1/2021/7"
    )
    ws["A2"].font = Font(size=10, color="1F4E79")

    headers = ["Name", "Role", "FOSC Hours", "BEOD Hours", "PTO Hours", "Total"]
    for col, label in enumerate(headers, 1):
        cell = ws.cell(4, col, label)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center
        cell.border = thin

    role_name = {
        Role.employee: "Employee",
        Role.supervisor: "Supervisor",
        Role.manager: "Manager",
    }
    row = 5
    first = row
    for emp in employees:
        fosc = beod = pto = 0.0
        day = q_start
        while day <= q_end:
            if day.weekday() < 5:
                data = _day_data(db, emp.id, day)
                fosc += float(data["fosc"] or 0)
                beod += float(data["beod"] or 0)
                pto += float(data["vacation"] or 0) + float(data["sick"] or 0)
                pto += float(data["covid"] or 0) + float(data["holiday"] or 0)
            day += timedelta(days=1)
        ws.cell(row, 1, emp.name).font = name_font
        ws.cell(row, 2, role_name.get(emp.role, "")).alignment = center
        for col, value, fill in (
            (3, round(fosc, 2), orange),
            (4, round(beod, 2), None),
            (5, round(pto, 2), pto_fill),
        ):
            cell = ws.cell(row, col, value)
            cell.number_format = "0.0"
            cell.alignment = center
            cell.border = thin
            if fill is not None:
                cell.fill = fill
        total = ws.cell(row, 6, f"=C{row}+D{row}+E{row}")
        total.number_format = "0.0"
        total.font = Font(bold=True)
        total.fill = peach
        total.alignment = center
        total.border = thin
        ws.cell(row, 1).border = thin
        ws.cell(row, 2).border = thin
        row += 1

    last = row - 1
    ws.cell(row, 1, "Total").font = Font(bold=True, color="FFFFFF", size=12)
    ws.cell(row, 1).fill = header_fill
    ws.cell(row, 2, "").fill = header_fill
    for col, letter in ((3, "C"), (4, "D"), (5, "E"), (6, "F")):
        cell = ws.cell(row, col, f"=SUM({letter}{first}:{letter}{last})" if last >= first else 0)
        cell.number_format = "0.0"
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = center
        cell.border = thin

    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 16
    for letter in "CDEF":
        ws.column_dimensions[letter].width = 16
    ws.freeze_panes = "A5"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf

def template_path() -> Path:
    return BASE_DIR / TEMPLATE_FILENAME
