#!/usr/bin/env python3
"""Import RBI PSI infrastructure from each release page's own-month column.

Run from any directory: python3 scripts/import_rbi_psi_pages.py

The official release-page URLs are taken from the checked-in link index. This
script fetches all 51 pages (January 2022 through March 2026), archives their
raw HTML, validates each release month, and extracts Part III counts in lakh. It
publishes files only after every page has been parsed successfully. The source
extract records each release URL and a SHA-256 hash of its HTML response.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


REPO = Path(__file__).resolve().parents[1]
RBI_ARCHIVE = REPO / "source_archive" / "rbi"
LINK_INDEX = RBI_ARCHIVE / "rbi_psi_release_links_2022-01_2026-03.csv"
EXTRACT = RBI_ARCHIVE / "rbi_psi_infrastructure_own_release_extract_2022-01_2026-03.csv"
MANIFEST = REPO / "source_manifest.csv"
YEAR_ROOT = REPO / "by_year"
SOURCE_ID = "RBI_PSI_INFRASTRUCTURE_OWN_MONTH_PANEL"
START_MONTH, END_MONTH = "2022-01", "2026-03"
MAX_RESPONSE_BYTES = 5_000_000
MAX_WORKERS = 4
MAX_ATTEMPTS = 3
# Keep the downloaded source pages beside the extracted data. They also let a
# failed run resume without re-fetching pages that were already parsed.
CACHE_DIR = RBI_ARCHIVE / "html"
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
FIELD_PATTERNS = {
    "credit_cards_outstanding": re.compile(r"^1\.1\s+credit cards\b", re.I),
    "debit_cards_outstanding": re.compile(r"^1\.2\s+debit cards\b", re.I),
    "atms": re.compile(r"^3\.?\s+number of atms(?: and crms)?\b", re.I),
    "pos_terminals": re.compile(r"^5\.?\s+number of pos terminals\b", re.I),
    "bharat_qr_codes": re.compile(r"^6\.?\s+bharat qr\b", re.I),
    "upi_qr_codes": re.compile(r"^7\.?\s+upi qr\b", re.I),
}
EXTRACT_FIELDS = [
    "month", *FIELD_PATTERNS, "unit", "rbi_release_id",
    "rbi_release_page_url", "archived_html_file", "source_html_sha256",
]
YEAR_FIELDS = ["month", *FIELD_PATTERNS, "unit", "source_file"]


def month_range(start: str, end: str) -> list[str]:
    year, month = map(int, start.split("-"))
    result = []
    while f"{year:04d}-{month:02d}" <= end:
        result.append(f"{year:04d}-{month:02d}")
        year += month // 12
        month = month % 12 + 1
    return result


def validate_url(url: str) -> None:
    """Keep the index and any HTTP redirects on RBI's public release pages."""
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != "www.rbi.org.in"
            or parts.username or parts.password or parts.port not in (None, 443)
            or parts.path.lower() != "/scripts/psiuserview.aspx"
            or not re.fullmatch(r"Id=\d+", parts.query, re.I)
            or parts.fragment):
        raise ValueError(f"Unexpected RBI release URL: {url!r}")


class RBIOnlyRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        validate_url(new_url)
        return super().redirect_request(request, fp, code, message, headers, new_url)


class TableRows(HTMLParser):
    """Collect text cells by HTML table row, including rows in nested tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row_stack: list[list[str]] = []
        self._cell_stack: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row_stack.append([])
        elif tag in ("td", "th") and self._row_stack:
            self._cell_stack.append([])
        elif tag == "br" and self._cell_stack:
            self._cell_stack[-1].append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell_stack:
            self._cell_stack[-1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row_stack and self._cell_stack:
            self._row_stack[-1].append("".join(self._cell_stack.pop()))
        elif tag == "tr" and self._row_stack:
            self.rows.append(self._row_stack.pop())


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-")).strip()


def parse_number(text: str) -> Decimal | None:
    cleaned = normalise(text).replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0:
        raise ValueError(f"Invalid infrastructure count: {text!r}")
    return value


def parse_release(html_bytes: bytes, month: str) -> dict[str, str]:
    year, number = map(int, month.split("-"))
    release_title = f"Payment System Indicators - {MONTH_NAMES[number - 1]} {year}"
    parser = TableRows()
    parser.feed(html_bytes.decode("utf-8", errors="replace"))
    rows = [[normalise(cell) for cell in row] for row in parser.rows]
    # RBI's December 2024 release title spells the month "Decmber"; its
    # infrastructure table header still correctly labels the month December.
    accepted_titles = {release_title}
    if month == "2024-12":
        accepted_titles.add("Payment System Indicators - Decmber 2024")
    if not any(title in row for title in accepted_titles for row in rows):
        raise ValueError(f"Release title does not match {month}: expected {release_title!r}")

    starts = [index for index, row in enumerate(rows)
              if "PART III - Payment Infrastructures (lakh)" in row]
    if len(starts) != 1:
        raise ValueError(f"{month}: expected one Part III infrastructure table; found {len(starts)}")
    start = starts[0] + 1
    ends = [index for index in range(start, len(rows))
            if any(cell.startswith("PART IV -") for cell in rows[index])]
    # Older releases end after Part III; later ones continue into Part IV.
    end = ends[0] if ends else len(rows)
    header_rows = rows[start:min(start + 8, end)]
    if not any(row and row[-1].casefold() == MONTH_NAMES[number - 1].casefold()
               for row in header_rows):
        raise ValueError(f"{month}: final Part III column is not labelled {MONTH_NAMES[number - 1]}")

    found: dict[str, str] = {}
    for row in rows[start:end]:
        if not row:
            continue
        label = row[0]
        for field, pattern in FIELD_PATTERNS.items():
            if not pattern.search(label):
                continue
            if field in found:
                raise ValueError(f"{month}: duplicate {field} row in Part III")
            values = [value for cell in row[1:]
                      if (value := parse_number(cell)) is not None]
            if len(values) != 4:
                raise ValueError(f"{month}: {field} has {len(values)} numeric columns, expected 4")
            found[field] = str(values[-1])  # RBI's final column is this release month.
            break
    missing = set(FIELD_PATTERNS) - found.keys()
    if missing:
        raise ValueError(f"{month}: missing Part III rows: {', '.join(sorted(missing))}")
    return found


def read_index() -> list[dict[str, str]]:
    with LINK_INDEX.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"month", "release_id", "release_page_url"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"Missing required columns in {LINK_INDEX}")
        rows = list(reader)
    expected = month_range(START_MONTH, END_MONTH)
    if [row["month"] for row in rows] != expected:
        raise ValueError(f"Expected exactly {len(expected)} ordered months {START_MONTH} to {END_MONTH}")
    for row in rows:
        validate_url(row["release_page_url"])
        if not row["release_id"].isdigit():
            raise ValueError(f"Invalid release ID for {row['month']}")
        if not row["release_page_url"].endswith(f"Id={row['release_id']}"):
            raise ValueError(f"Release ID and URL disagree for {row['month']}")
    return rows


def fetch_one(index_row: dict[str, str]) -> dict[str, str]:
    month, url = index_row["month"], index_row["release_page_url"]
    cache_path = CACHE_DIR / f"{month}_id{index_row['release_id']}.html"
    opener = build_opener(RBIOnlyRedirect())

    def result(raw: bytes) -> dict[str, str]:
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError(f"{month}: HTML response exceeds {MAX_RESPONSE_BYTES} bytes")
        values = parse_release(raw, month)  # Also validates any cached HTML.
        return {
            "month": month, **values, "unit": "lakh",
            "rbi_release_id": index_row["release_id"],
            "rbi_release_page_url": url,
            "archived_html_file": str(cache_path.relative_to(REPO)),
            "source_html_sha256": hashlib.sha256(raw).hexdigest(),
        }

    if cache_path.exists():
        try:
            return result(cache_path.read_bytes())
        except (OSError, ValueError):
            # A corrupt or stale cache entry is replaced only after a fresh
            # official response has passed the same release-month checks.
            pass
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            request = Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; AcademicDataCollector/1.0)",
                "Accept": "text/html",
            })
            with opener.open(request, timeout=45) as response:
                validate_url(response.geturl())
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            parsed = result(raw)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=f".{cache_path.name}.", dir=CACHE_DIR)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, cache_path)
            finally:
                temporary.unlink(missing_ok=True)
            return parsed
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"{month} {url}: {exc}") from exc
            time.sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable")


def csv_bytes(rows: list[dict[str, str]], fields: list[str], newline: str = "\n") -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator=newline)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def manifest_bytes(extract_bytes: bytes, last_url: str) -> bytes:
    original = MANIFEST.read_bytes()
    newline = "\r\n" if b"\r\n" in original else "\n"
    with io.StringIO(original.decode("utf-8-sig"), newline="") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    required = {"source_id", "owner", "series", "source_period", "source_url",
                "downloaded_file", "retrieved_utc", "sha256", "status", "notes"}
    if not required.issubset(fields):
        raise ValueError(f"Unexpected manifest columns in {MANIFEST}")
    record = {
        "source_id": SOURCE_ID,
        "owner": "RBI",
        "series": "Payment System Indicators - payment infrastructure",
        "source_period": f"{START_MONTH} to {END_MONTH}",
        "source_url": last_url,
        "downloaded_file": f"data/assignment2/{EXTRACT.relative_to(REPO)}",
        "retrieved_utc": datetime.now(timezone.utc).date().isoformat(),
        "sha256": hashlib.sha256(extract_bytes).hexdigest(),
        "status": "extracted_from_official_release_pages",
        "notes": ("51 own-month Part III observations in lakh; each row records its RBI "
                  "release URL, local HTML file, and SHA-256 of the HTML response. "
                  "Raw HTML is archived under source_archive/rbi/html/."),
    }
    matches = [i for i, row in enumerate(rows) if row["source_id"] == SOURCE_ID]
    if len(matches) > 1:
        raise ValueError(f"Duplicate {SOURCE_ID} entries in {MANIFEST}")
    if matches:
        rows[matches[0]] = record
    else:
        rows.append(record)
    return csv_bytes(rows, fields, newline)


def output_files(rows: list[dict[str, str]]) -> dict[Path, bytes]:
    extract = csv_bytes(rows, EXTRACT_FIELDS)
    files = {EXTRACT: extract}
    by_month = {row["month"]: row for row in rows}
    for year in sorted({month[:4] for month in by_month}):
        path = YEAR_ROOT / year / "rbi_psi_infrastructure.csv"
        merged: dict[str, dict[str, str]] = {}
        if path.exists():
            with path.open(newline="", encoding="utf-8-sig") as stream:
                for old in csv.DictReader(stream):
                    if old["month"] in merged:
                        raise ValueError(f"Duplicate existing month {old['month']} in {path}")
                    merged[old["month"]] = old
        for month, row in by_month.items():
            if month[:4] == year:
                merged[month] = {
                    "month": month, **{field: row[field] for field in FIELD_PATTERNS},
                    "unit": "lakh", "source_file": EXTRACT.name,
                }
        files[path] = csv_bytes([merged[month] for month in sorted(merged)], YEAR_FIELDS)
    files[MANIFEST] = manifest_bytes(extract, rows[-1]["rbi_release_page_url"])
    return files


def publish_atomically(files: dict[Path, bytes]) -> None:
    """Stage every file first; restore originals if any replacement fails."""
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            temporary = Path(name)
            staged[path] = temporary
            if path.exists():
                os.chmod(temporary, path.stat().st_mode & 0o777)
            else:
                os.chmod(temporary, 0o644)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        for path in files:
            if path.exists():
                fd, name = tempfile.mkstemp(prefix=f".{path.name}.backup.", dir=path.parent)
                os.close(fd)
                backup = Path(name)
                backups[path] = backup
                shutil.copy2(path, backup)
        for path in files:
            os.replace(staged[path], path)
            replaced.append(path)
    except BaseException:
        for path in reversed(replaced):
            if path in backups:
                os.replace(backups[path], path)
            else:
                path.unlink(missing_ok=True)
        raise
    finally:
        for temporary in (*staged.values(), *backups.values()):
            temporary.unlink(missing_ok=True)


def main() -> None:
    index = read_index()
    results: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_one, row): row["month"] for row in index}
        for future in as_completed(futures):
            month = futures[future]
            try:
                results[month] = future.result()
                print(f"Validated {month}", file=sys.stderr)
            except Exception as exc:
                errors.append(str(exc))
    if errors:
        raise SystemExit("No files changed; RBI releases failed:\n" + "\n".join(sorted(errors)))
    rows = [results[row["month"]] for row in index]
    publish_atomically(output_files(rows))
    print(f"Imported {len(rows)} official RBI Part III own-month infrastructure rows")


if __name__ == "__main__":
    main()
