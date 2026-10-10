#!/usr/bin/env python3
"""Import the pre-2022 series used for the mature-phase window and the spliced long series.

Run from the repository root: python3 scripts/import_card_history.py
Standard library only. Network access is needed once, for RBI PSI pages not yet archived.

Inputs and outputs:
  RBI PSI release pages, Id 1-12 (June 2021 - May 2022), www.rbi.org.in/Scripts/PSIUserView.aspx?Id=<n>
      archived as source_archive/rbi/html/<YYYY-MM>_id<n>.html
      -> by_year/2021/rbi_psi_card_ppi_own_month.csv and rbi_psi_infrastructure.csv (Jun-Dec 2021)
      -> by_year/{2020,2021}/rbi_psi_card_prior_year_column.csv: Jun 2020 - May 2021 card totals
         as printed in the following year's release (the "same month last year" column)
      -> source_archive/rbi/rbi_psi_card_history_extract_2020-06_2022-05.csv (every parsed cell)
  RBI bank-wise ATM/POS/card pages in the pre-March-2022 layouts (source_archive/rbi/bankwise_html/)
      -> by_year/<YYYY>/rbi_bankwise_card_totals.csv: the published all-bank "Total" row, Jun 2014 - Feb 2022
  MoSPI CPI Annexure VI (base 2012), source_archive/mospi/cpi_combined_2012_base_annexure_vi_2013-01_2025-12.csv
      -> by_year/<YYYY>/mospi_cpi_combined_monthly.csv for 2014-2021 (2022 onward is left untouched)
  NPCI P2P/P2M JSON, source_archive/npci/ecosystem_api/p2p-and-p2m-transactions/<YYYY-MM>.json
      -> by_year/{2020,2021}/npci_upi_p2p_p2m_transactions.csv (Apr 2020 - Dec 2021; NPCI has no earlier split)

Old bank-wise layouts report card use only "at ATM" and "at PoS". Summed over banks, that PoS
column equals the PSI total of PoS + Others (online) card payments: credit Jan 2022 195.81 mn in
both sources. The column is therefore stored as card *payments* (credit_payments_n etc.).
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "source_archive"
YEAR_ROOT = REPO / "by_year"
PSI_HTML = ARCHIVE / "rbi" / "html"
PSI_URL = "https://www.rbi.org.in/Scripts/PSIUserView.aspx?Id={}"
PSI_EXTRACT = ARCHIVE / "rbi" / "rbi_psi_card_history_extract_2020-06_2022-05.csv"
CPI_SOURCE = ARCHIVE / "mospi" / "cpi_combined_2012_base_annexure_vi_2013-01_2025-12.csv"
CPI_URL = ("https://mospi.gov.in/uploads/latestReleases/latest_release_1768213461321_53cd35fd-1bbc-"
           "4b43-b92d-8fb67474ee74_Press_Release_of_CPI_for_December_2025.pdf")
CPI_LINK_FACTOR = 0.5267
NPCI_URL = "https://www.npci.org.in/product/ecosystem-statistics/upi"
MONTHS = "January February March April May June July August September October November December".split()
# PSI release Id 1 is June 2021; ids then run one per month.
PSI_IDS = {n: f"{2021 + (n + 4) // 12}-{(n + 4) % 12 + 1:02d}" for n in range(1, 13)}

CARD_ROWS = {  # PSI Part I label prefix -> field stem
    "4 Card Payments": "card_total", "4.1 Credit Cards": "credit_total",
    "4.1.1 PoS based": "credit_pos", "4.1.2 Others": "credit_other",
    "4.2 Debit Cards": "debit_total", "4.2.1 PoS based": "debit_pos", "4.2.2 Others": "debit_other",
    "5 Prepaid Payment Instruments": "ppi_total", "5.1 Wallets": "ppi_wallets",
    "5.2 Cards": "ppi_cards", "5.2.1 PoS based": "ppi_card_pos", "5.2.2 Others": "ppi_card_other",
}
INFRA_ROWS = {
    "1.1 Credit Cards": "credit_cards_outstanding", "1.2 Debit Cards": "debit_cards_outstanding",
    "3 Number of ATMs": "atms", "5 Number of PoS Terminals": "pos_terminals",
    "6 Bharat QR": "bharat_qr_codes", "7 UPI QR": "upi_qr_codes",
}
CARD_STEMS = list(dict.fromkeys(CARD_ROWS.values()))
CARD_FIELDS = (["month"] + [f"{s}_{u}" for s in CARD_STEMS for u in ("volume_lakh", "value_crore")]
               + ["rbi_release_id", "rbi_release_page_url", "official_xlsx_url"])
INFRA_FIELDS = ["month", *INFRA_ROWS.values(), "unit", "source_file"]

BANKWISE_FIELDS = [
    "month", "layout", "value_unit_published", "atms", "pos_terminals", "bharat_qr_codes",
    "credit_cards", "credit_atm_n", "credit_payments_n", "credit_atm_v_crore", "credit_payments_v_crore",
    "debit_cards", "debit_atm_n", "debit_payments_n", "debit_atm_v_crore", "debit_payments_v_crore",
    "source_file",
]


def rows_of(page: str) -> list[list[str]]:
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        cells = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split())
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
        out.append(cells)
    return out


def num(text: str) -> float | None:
    text = text.replace(",", "").strip()
    return float(text) if re.fullmatch(r"\d+(\.\d+)?", text) else None


def write_by_year(filename: str, fields: list[str], rows: list[dict[str, object]]) -> None:
    by_year: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_year[str(row["month"])[:4]].append(row)
    for year, items in sorted(by_year.items()):
        path = YEAR_ROOT / year / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(sorted(items, key=lambda r: str(r["month"])))
    print(f"{filename}: {len(rows)} rows in {', '.join(sorted(by_year))}")


# ---------- RBI PSI releases, June 2021 - May 2022 ----------

def psi_page(release_id: int, month: str) -> tuple[str, Path]:
    path = PSI_HTML / f"{month}_id{release_id}.html"
    if not path.exists():
        request = Request(PSI_URL.format(release_id),
                          headers={"User-Agent": "Mozilla/5.0 (compatible; AcademicDataCollector/1.0)"})
        with urlopen(request, timeout=60) as response:
            raw = response.read()
        PSI_HTML.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        time.sleep(0.5)
    text = path.read_text(encoding="utf-8", errors="replace")
    year, mm = month.split("-")
    if f"Payment System Indicators - {MONTHS[int(mm) - 1]} {year}" not in text:
        raise ValueError(f"PSI Id={release_id} is not the {month} release")
    return text, path


def section(rows: list[list[str]], start: str, stop: str | None) -> list[list[str]]:
    first = next(i for i, r in enumerate(rows) if r and r[0].startswith(start))
    last = next((i for i in range(first + 1, len(rows)) if stop and rows[i] and rows[i][0].startswith(stop)),
                len(rows))
    return rows[first + 1:last]


def match(label: str, wanted: dict[str, str]) -> str | None:
    # Longest prefix first so "4.1.1 PoS based" is not read as "4.1 Credit Cards".
    for prefix in sorted(wanted, key=len, reverse=True):
        if re.match(re.escape(prefix) + r"\b", label):
            return wanted[prefix]
    return None


def year_back(month: str) -> str:
    return f"{int(month[:4]) - 1}{month[4:]}"


def psi_history() -> None:
    extract, own, prior, infra = [], [], [], []
    for release_id, month in PSI_IDS.items():
        text, path = psi_page(release_id, month)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = rows_of(text)
        url = PSI_URL.format(release_id)
        cards: dict[str, list[float | None]] = {}
        for cells in section(rows, "PART I", "PART II"):
            stem = match(cells[0], CARD_ROWS) if cells else None
            if stem:
                values = [num(c) for c in cells[1:]]
                if len(values) != 8 or None in (values[1], values[3], values[5], values[7]):
                    raise ValueError(f"{month}: {cells[0]} has unexpected cells {cells[1:]}")
                cards[stem] = values
        if set(cards) != set(CARD_STEMS):
            raise ValueError(f"{month}: missing PSI card rows {set(CARD_STEMS) - set(cards)}")
        stock: dict[str, float] = {}
        for cells in section(rows, "PART III", "PART IV"):
            field = match(cells[0], INFRA_ROWS) if cells else None
            if field:
                stock[field] = num(cells[-1])
        if None in stock.values() or len(stock) != len(INFRA_ROWS):
            raise ValueError(f"{month}: incomplete Part III {stock}")
        for column, vol_i, val_i, target in (("own_month", 3, 7, month),
                                             ("same_month_previous_year", 1, 5, year_back(month))):
            row = {"month": target, "column": column, "release_month": month, "rbi_release_id": release_id,
                   "rbi_release_page_url": url, "archived_html_file": str(path.relative_to(REPO)),
                   "source_html_sha256": sha}
            for stem, values in cards.items():
                row[f"{stem}_volume_lakh"], row[f"{stem}_value_crore"] = values[vol_i], values[val_i]
            extract.append(row)
            (own if column == "own_month" else prior).append(row)
        infra.append({"month": month, **stock, "unit": "lakh", "source_file": str(path.relative_to(REPO))})
    with PSI_EXTRACT.open("w", newline="", encoding="utf-8") as stream:
        fields = ["month", "column", "release_month", "rbi_release_id"] + CARD_FIELDS[1:-3] + [
            "rbi_release_page_url", "archived_html_file", "source_html_sha256"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(extract)
    # Releases from January 2022 are already transcribed; check this parser reproduces them.
    published = {}
    with (YEAR_ROOT / "2022" / "rbi_psi_card_ppi_own_month.csv").open(encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            published[row["month"]] = row
    for row in own:
        if row["month"] in published:
            for field in CARD_FIELDS[1:-3]:
                if abs(float(published[row["month"]][field]) - row[field]) > 0.011:
                    raise ValueError(f"{row['month']} {field}: parsed {row[field]} vs transcribed "
                                     f"{published[row['month']][field]}")
    with (YEAR_ROOT / "2022" / "rbi_psi_infrastructure.csv").open(encoding="utf-8") as stream:
        published = {row["month"]: row for row in csv.DictReader(stream)}
    for row in infra:
        if row["month"] in published:
            for field in INFRA_ROWS.values():
                if abs(float(published[row["month"]][field]) - row[field]) > 0.011:
                    raise ValueError(f"{row['month']} {field}: parsed {row[field]} vs imported "
                                     f"{published[row['month']][field]}")
    print(f"PSI parser reproduces the transcribed Jan-May 2022 card, PPI and infrastructure rows")
    for row in own:
        row["official_xlsx_url"] = ""
    write_by_year("rbi_psi_card_ppi_own_month.csv", CARD_FIELDS, [r for r in own if r["month"] < "2022-01"])
    write_by_year("rbi_psi_infrastructure.csv", INFRA_FIELDS, [r for r in infra if r["month"] < "2022-01"])
    write_by_year("rbi_psi_card_prior_year_column.csv",
                  ["month"] + CARD_FIELDS[1:15] + ["release_month", "rbi_release_page_url"], prior)


# ---------- RBI bank-wise national totals, old layouts ----------

def bankwise_totals() -> None:
    out = []
    for path in sorted((ARCHIVE / "rbi" / "bankwise_html").glob("atmid_*.html")):
        page = path.read_text(encoding="utf-8", errors="ignore")
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())
        if "Online (e-com)" in text:
            continue  # March 2022 onward: 26-column layout, read by import_landscape_sources.py
        found = re.search(r"Statistics[^0-9]{0,60}?(" + "|".join(MONTHS) + r")[\s,'-]*(\d{2,4})\b", text)
        if not found:
            raise ValueError(f"{path.name}: no month in heading")
        year = int(found.group(2)) + (2000 if len(found.group(2)) == 2 else 0)
        month = f"{year}-{MONTHS.index(found.group(1)) + 1:02d}"
        header = text[text.find("Bank Name"):text.find("Bank Name") + 900].lower()
        totals = []
        for cells in rows_of(page):
            label = next((c for c in cells if c), "")
            values = [num(c) for c in cells if num(c) is not None]
            if re.fullmatch(r"(grand )?total", label, re.I) and len(values) >= 14:
                totals.append(values)
        if not totals:
            raise ValueError(f"{path.name}: no Total row")
        values = max(totals, key=lambda v: v[-5])  # all-bank total has the most debit cards
        width = len(values)
        if width not in (14, 16):
            raise ValueError(f"{path.name}: Total row has {width} numbers")
        # Value units: "Rs. Millions" to June 2019, "Rupees Lakh" from May 2020. Pages without a
        # unit label (Aug and Nov 2016, Jul 2019 - Apr 2020) follow the scale of their neighbours:
        # credit-card payments Jun 2019 Rs 569,284 mn = Rs 56,928 crore; Jul 2019 5,961,639 lakh = Rs 59,616 crore.
        if "million" in header:
            unit, to_crore = "Rs million", 0.1
        elif "lakh" in header:
            unit, to_crore = "Rs lakh", 0.01
        elif month <= "2019-06":
            unit, to_crore = "Rs million (unlabelled)", 0.1
        else:
            unit, to_crore = "Rs lakh (unlabelled)", 0.01
        card = values[-10:]
        out.append({
            "month": month, "layout": f"{width}-column", "value_unit_published": unit,
            "atms": values[0] + values[1], "pos_terminals": values[2] + values[3],
            "bharat_qr_codes": values[5] if width == 16 else "",
            "credit_cards": card[0], "credit_atm_n": card[1], "credit_payments_n": card[2],
            "credit_atm_v_crore": round(card[3] * to_crore, 2), "credit_payments_v_crore": round(card[4] * to_crore, 2),
            "debit_cards": card[5], "debit_atm_n": card[6], "debit_payments_n": card[7],
            "debit_atm_v_crore": round(card[8] * to_crore, 2), "debit_payments_v_crore": round(card[9] * to_crore, 2),
            "source_file": f"source_archive/rbi/bankwise_html/{path.name}",
        })
    months = sorted(r["month"] for r in out)
    if len(set(months)) != len(months):
        raise ValueError("Duplicate bank-wise months")
    write_by_year("rbi_bankwise_card_totals.csv", BANKWISE_FIELDS, out)


# ---------- MoSPI CPI and NPCI P2P/P2M back-series ----------

def cpi_history() -> None:
    rows = []
    with CPI_SOURCE.open(encoding="utf-8") as stream:
        for line in csv.DictReader(stream):
            for i, name in enumerate(MONTHS):
                month = f"{line['year']}-{i + 1:02d}"
                if "2014-01" <= month <= "2021-12":
                    native = float(line[name[:3].lower()])
                    rows.append({
                        "month": month, "cpi_combined_index_native": native, "native_base_year": 2012,
                        "status": "final", "link_factor_to_2024": CPI_LINK_FACTOR,
                        "cpi_combined_index_linked_2024": round(native * CPI_LINK_FACTOR, 5),
                        "source_url": CPI_URL,
                        "notes": "Official MoSPI Annexure VI time series; linked value is mechanical "
                                 "application of official Combined factor 0.5267.",
                    })
    write_by_year("mospi_cpi_combined_monthly.csv", list(rows[0]), rows)


def npci_split_history() -> None:
    rows = []
    for path in sorted((ARCHIVE / "npci" / "ecosystem_api" / "p2p-and-p2m-transactions").glob("*.json")):
        if path.stem >= "2022-01":
            continue  # 2022 onward is the checked transcription; the JSON agrees with it
        r = json.loads(path.read_text(encoding="utf-8"))["data"]["results"][0]
        n = {k: float(str(r[k]).replace(",", "").strip()) for k in r if k.endswith(("_mn", "_cr"))}
        if abs(n["p_2_p_volume_mn"] + n["p_2_m_volume_mn"] - n["total_volume_mn"]) > 0.02:
            raise ValueError(f"{path.stem}: P2P + P2M does not add to total")
        rows.append({
            "month": f"{path.stem}-01", "total_volume_mn": n["total_volume_mn"],
            "total_value_crore": n["total_value_cr"], "p2p_volume_mn": n["p_2_p_volume_mn"],
            "p2p_value_crore": n["p_2_p_value_cr"], "p2m_volume_mn": n["p_2_m_volume_mn"],
            "p2m_value_crore": n["p_2_m_value_cr"], "source_month_label": r["month_value"],
            "source_file": str(path.relative_to(REPO)), "source_url": NPCI_URL,
        })
    write_by_year("npci_upi_p2p_p2m_transactions.csv", list(rows[0]), rows)


def main() -> None:
    psi_history()
    bankwise_totals()
    cpi_history()
    npci_split_history()


if __name__ == "__main__":
    main()
