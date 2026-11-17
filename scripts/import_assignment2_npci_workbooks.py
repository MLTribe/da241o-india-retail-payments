#!/usr/bin/env python3
"""Split NPCI's fiscal-year UPI and IMPS product-statistics workbooks into calendar-year CSVs.

Inputs: the original monthly workbooks archived in source_archive/npci/, one per fiscal year
(Product-Statistics-<UPI|IMPS>-Product-statistics-<upi|imps>-YYYY-YY-Monthly.xlsx; FY2025-26
UPI is npci_upi_product_statistics_FY2025-26.xlsx). NPCI serves each workbook from the
"Download" button of www.npci.org.in/product/<upi|imps>/product-statistics.

Outputs: by_year/<YYYY>/npci_upi_product_statistics.csv (month, banks_live, volume_mn,
value_crore, source_file, source_url) and npci_imps_product_statistics.csv (member_banks in
place of banks_live). Volume is in millions and value in ₹ crore, as published.

Months after IMPORT_END are left out so every by_year series ends in the same month; a
workbook may already carry later months. A month that a workbook repeats must agree, and
every month from April 2016 to IMPORT_END must be present. Standard library only.

Run from the repository root: python3 scripts/import_assignment2_npci_workbooks.py
"""

from __future__ import annotations

import csv
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree

REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "source_archive" / "npci"
YEAR_ROOT = REPO / "by_year"
IMPORT_START, IMPORT_END = "2016-04", "2026-08"
MONTHS = "January February March April May June July August September October November December".split()
SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
PRODUCTS = {
    "upi": {"pattern": r"(?i)(Product-Statistics-UPI-.*|npci_upi_product_statistics_FY.*)\.xlsx$",
            "count_header": "No. of Banks live on UPI", "count_field": "banks_live",
            "output": "npci_upi_product_statistics.csv",
            "url": "https://www.npci.org.in/product/upi/product-statistics"},
    "imps": {"pattern": r"(?i)Product-Statistics-IMPS-.*\.xlsx$",
             "count_header": "No. of Member Banks", "count_field": "member_banks",
             "output": "npci_imps_product_statistics.csv",
             "url": "https://www.npci.org.in/product/imps/product-statistics"},
}


def sheet_rows(path: Path) -> list[list[str]]:
    """Cell text of the first worksheet, row by row (shared and inline strings resolved)."""
    with zipfile.ZipFile(path) as book:
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            for item in ElementTree.fromstring(book.read("xl/sharedStrings.xml")).iter(f"{SHEET_NS}si"):
                shared.append("".join(t.text or "" for t in item.iter(f"{SHEET_NS}t")))
        sheet = sorted(n for n in book.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))[0]
        rows = []
        for row in ElementTree.fromstring(book.read(sheet)).iter(f"{SHEET_NS}row"):
            cells = []
            for cell in row.findall(f"{SHEET_NS}c"):
                value = cell.find(f"{SHEET_NS}v")
                if cell.get("t") == "s" and value is not None:
                    cells.append(shared[int(value.text)])
                elif cell.get("t") == "inlineStr":
                    cells.append("".join(t.text or "" for t in cell.iter(f"{SHEET_NS}t")))
                else:
                    cells.append(value.text if value is not None and value.text else "")
            rows.append(cells)
    return rows


def number(text: str) -> float:
    """NPCI groups digits Indian-style and sometimes misplaces a comma (April 2026 UPI value
    "29,029,88.05"); commas are separators only, so they are dropped."""
    cleaned = text.replace(",", "").strip()
    if not re.fullmatch(r"\d+(\.\d+)?", cleaned):
        raise ValueError(f"Not a number: {text!r}")
    return float(cleaned)


def month_key(label: str) -> str:
    found = re.fullmatch(r"\s*([A-Za-z]+)[-\s]+(\d{4})\s*", label)
    if not found or found.group(1).capitalize() not in MONTHS:
        raise ValueError(f"Unexpected month label {label!r}")
    return f"{found.group(2)}-{MONTHS.index(found.group(1).capitalize()) + 1:02d}"


def month_range(start: str, end: str) -> list[str]:
    out, (year, month) = [], map(int, start.split("-"))
    while f"{year:04d}-{month:02d}" <= end:
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def import_product(product: str, spec: dict[str, str]) -> None:
    rows: dict[str, dict[str, object]] = {}
    for path in sorted(p for p in ARCHIVE.glob("*.xlsx") if re.match(spec["pattern"], p.name)):
        table = sheet_rows(path)
        expected = ["Month", spec["count_header"], "Volume (In Mn.)", "Value (In Cr.)"]
        if [cell.strip() for cell in table[0][:4]] != expected:
            raise ValueError(f"{path.name}: header {table[0]} is not {expected}")
        for cells in table[1:]:
            if not any(cell.strip() for cell in cells):
                continue
            month = month_key(cells[0])
            row = {"month": month, spec["count_field"]: int(number(cells[1])),
                   "volume_mn": number(cells[2]), "value_crore": number(cells[3]),
                   "source_file": path.name, "source_url": spec["url"]}
            if month in rows:
                old = rows[month]
                if any(old[k] != row[k] for k in (spec["count_field"], "volume_mn", "value_crore")):
                    raise ValueError(f"{product} {month}: {old['source_file']} and {path.name} disagree")
                continue
            rows[month] = row
    wanted = month_range(IMPORT_START, IMPORT_END)
    missing = [m for m in wanted if m not in rows]
    if missing:
        raise ValueError(f"{product}: workbooks lack {missing}")
    later = sorted(m for m in rows if m > IMPORT_END)
    by_year: dict[str, list[dict[str, object]]] = defaultdict(list)
    for month in wanted:
        by_year[month[:4]].append(rows[month])
    fields = ["month", spec["count_field"], "volume_mn", "value_crore", "source_file", "source_url"]
    for year, items in sorted(by_year.items()):
        with (YEAR_ROOT / year / spec["output"]).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(items)
    note = f"; not imported (after {IMPORT_END}): {', '.join(later)}" if later else ""
    print(f"{spec['output']}: {len(wanted)} months {IMPORT_START} to {IMPORT_END} in {len(by_year)} files{note}")


def main() -> None:
    for product, spec in PRODUCTS.items():
        import_product(product, spec)


if __name__ == "__main__":
    main()
