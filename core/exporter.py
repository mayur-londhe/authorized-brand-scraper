"""
Exports a list of DealerRecord objects to an xlsx logbook.
"""
from datetime import datetime
from pathlib import Path
from typing import List

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .schema import DealerRecord

COLUMNS = [
    ("Source Brand",    "source_brand"),
    ("Category",        "category"),
    ("State Query",     "state_name"),
    ("Dealer Name",     "name"),
    ("Phone",           "phone"),
    ("Email",           "email"),
    ("Address",         "address"),
    ("City",            "city"),
    ("State",           "state"),
    ("Pincode",         "pincode"),
    ("Dealer Type",     "dealer_type"),
    ("Website",         "website"),
    ("Latitude",        "latitude"),
    ("Longitude",       "longitude"),
]

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
ALT_FILL    = PatternFill("solid", fgColor="D6E4F0")

thin = Side(style="thin", color="BFBFBF")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)


def export_to_xlsx(records: List[DealerRecord], output_dir: Path, filename: str = "") -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"dealers_{ts}.xlsx"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Dealer Logbook"

    # ── Header row ────────────────────────────────────────────────────
    for col_idx, (header, _) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font   = HEADER_FONT
        cell.fill   = HEADER_FILL
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    ws.row_dimensions[1].height = 22

    # ── Data rows ─────────────────────────────────────────────────────
    for row_idx, record in enumerate(records, start=2):
        rec_dict = record.to_dict()
        fill = ALT_FILL if row_idx % 2 == 0 else None

        for col_idx, (_, field_key) in enumerate(COLUMNS, start=1):
            val = rec_dict.get(field_key, "")
            cell = ws.cell(row=row_idx, column=col_idx, value=val or "")
            cell.border = BORDER
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if fill:
                cell.fill = fill

    # ── Column widths ─────────────────────────────────────────────────
    col_widths = [16, 22, 18, 28, 18, 28, 38, 18, 18, 12, 20, 28, 12, 12]
    for i, w in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # ── Freeze panes & auto-filter ────────────────────────────────────
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    out_path = output_dir / filename
    wb.save(out_path)
    print(f"[Exporter] Saved {len(records)} records → {out_path}")
    return out_path
