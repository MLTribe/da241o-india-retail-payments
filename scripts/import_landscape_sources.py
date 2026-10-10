#!/usr/bin/env python3
"""Normalise the bank-, app- and state-level sources archived for the landscape analysis.

Inputs (all under source_archive/):
  rbi/bankwise_html/atmid_<n>.html   RBI "Bank-wise ATM/POS/Card Statistics" monthly pages
                                     (www.rbi.org.in/Scripts/ATMView.aspx?atmid=<n>)
  npci/ecosystem_api/<tab>/<YYYY-MM>.json
                                     NPCI UPI ecosystem statistics (scripts/fetch_npci_ecosystem.py)
  phonepe_pulse/aggregated_transaction_state/<state>/<year>/<q>.json
                                     PhonePe Pulse open data (CDLA-Permissive-2.0)

Outputs (annual CSVs under by_year/<YYYY>/):
  rbi_bankwise_cards.csv        month, bank, bank_group, infrastructure and card-use fields
  npci_upi_apps.csv             month, app, volume_mn, value_crore
  npci_upi_member_banks.csv     month, bank, volume_mn, value_crore
  npci_upi_states.csv           month, state, volume_mn, value_crore (districts summed)
  phonepe_pulse_states.csv      quarter, state, p2p_count, utility_count, merchant_count

Run from the repository root: python3 scripts/import_landscape_sources.py
Standard library only. Only the 26-column RBI layout (March 2022 onward) is read; the
earlier layout does not split card payments into PoS and online.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "source_archive"
YEAR_ROOT = REPO / "by_year"
MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
SHORT = {m[:3]: i for m, i in MONTHS.items()}

# Data columns 1–26 of the RBI bank-wise table, in published order.
RBI_FIELDS = [
    "atms_onsite", "atms_offsite", "pos_terminals", "micro_atms", "bharat_qr_codes", "upi_qr_codes",
    "credit_cards", "debit_cards",
    "credit_pos_n", "credit_pos_v", "credit_online_n", "credit_online_v", "credit_other_n",
    "credit_other_v", "credit_atm_cash_n", "credit_atm_cash_v",
    "debit_pos_n", "debit_pos_v", "debit_online_n", "debit_online_v", "debit_other_n",
    "debit_other_v", "debit_atm_cash_n", "debit_atm_cash_v", "debit_pos_cash_n", "debit_pos_cash_v",
]
RBI_GROUPS = {"public sector banks": "Public sector", "private sector banks": "Private sector",
              "foreign banks": "Foreign", "payment banks": "Payments bank",
              "small finance banks": "Small finance"}


class TableRows(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] = []
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.in_cell, self.cell = True, []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.row is not None and self.in_cell:
            self.row.append(" ".join("".join(self.cell).split()))
            self.in_cell = False
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)


def number(text: str) -> float:
    text = str(text).replace(",", "").replace("%", "").strip()
    return float(text) if text not in ("", "-", "NA") else 0.0


BANK_ALIASES = {
    "IDBI": "IDBI BANK", "IDFC BANK": "IDFC FIRST BANK", "DBS BANK": "DBS BANK INDIA",
    "DBS INDIA BANK": "DBS BANK INDIA", "INDIA POST PAYMENT BANK": "INDIA POST PAYMENTS BANK",
}


def bank_key(name: str) -> str:
    """One name per bank across RBI and NPCI releases: upper case, no punctuation or legal suffix."""
    name = re.sub(r"[^A-Z0-9 ]", " ", name.upper().replace("&", " AND "))
    name = " ".join(re.sub(r"\b(LTD|LIMITED|THE|CO|PLC|N A|NA)\b", " ", name).split())
    return BANK_ALIASES.get(name, name)


STATE_ALIASES = {
    "ANDAMAN AND NICOBAR": "ANDAMAN AND NICOBAR ISLANDS",
    "DADRA AND NAGAR HAVELI": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
    "DADRA AND NAGAR HAVELI AND DAMAN DIU": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
    "DAMAN AND DIU": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
    "NCT OF DELHI": "DELHI", "ORISSA": "ODISHA", "PONDICHERRY": "PUDUCHERRY",
    "JAMMU KASHMIR": "JAMMU AND KASHMIR", "UTTARANCHAL": "UTTARAKHAND",
}


def state_key(name: str) -> str:
    """One spelling per state/UT across NPCI releases and PhonePe Pulse folder names."""
    name = re.sub(r"[#*]+|\s+TOTAL$", "", name.strip().upper().replace("-", " ")).strip()
    name = " ".join(name.replace("&", " AND ").split())
    return STATE_ALIASES.get(name, name)


def write_by_year(filename: str, header: list[str], rows: list[list[object]], period_index: int = 0) -> None:
    by_year: dict[str, list[list[object]]] = defaultdict(list)
    for row in rows:
        by_year[str(row[period_index])[:4]].append(row)
    for year, items in sorted(by_year.items()):
        folder = YEAR_ROOT / year
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / filename).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(sorted(items, key=lambda r: [str(c) for c in r[:2]]))
    print(f"{filename}: {len(rows)} rows in {len(by_year)} annual files")


def rbi_bankwise() -> None:
    rows, skipped = [], []
    for path in sorted((ARCHIVE / "rbi" / "bankwise_html").glob("atmid_*.html")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        found = re.search(r"Statistics for the Month of\s*([A-Za-z]+)[\s-]*(\d{2,4})", text)
        if not found:
            skipped.append(f"{path.name}: no month heading (pre-March 2022 layout)")
            continue
        month_no = MONTHS.get(found.group(1).lower()) or SHORT[found.group(1).lower()[:3]]
        year = int(found.group(2)) + (2000 if len(found.group(2)) == 2 else 0)
        month = f"{year:04d}-{month_no:02d}"
        parser = TableRows()
        parser.feed(text)
        group = None
        for cells in parser.rows:
            label = cells[0].lower() if cells else ""
            if label in RBI_GROUPS and not any(cells[1:]):
                group = RBI_GROUPS[label]
                continue
            if len(cells) != 28 or not re.fullmatch(r"\d+", cells[0]) or group is None:
                continue
            values = [number(c) for c in cells[2:]]
            rows.append([month, bank_key(cells[1]), cells[1], group] + values)
    write_by_year("rbi_bankwise_cards.csv", ["month", "bank", "bank_name_as_published", "bank_group"]
                  + RBI_FIELDS, rows)
    for line in skipped:
        print("  skipped", line)


def npci_tab(tab: str):
    for path in sorted((ARCHIVE / "npci" / "ecosystem_api" / tab).glob("*.json")):
        results = (json.loads(path.read_text(encoding="utf-8")).get("data") or {}).get("results")
        if results:
            yield path.stem, results


APP_PATTERNS = [
    (r"^phone ?pe$", "PhonePe"), (r"^paytm|^paytmwallet$", "Paytm"), (r"^whatsapp$", "WhatsApp"),
    (r"^fam ?(app|pay)|^fam$", "FamApp"), (r"^mobikwik", "Mobikwik"), (r"^cred$", "CRED"),
]


def app_key(name: str) -> str:
    """One name per app: NPCI renames apps (Paytm Payments Bank App -> Paytm) and varies spelling."""
    name = re.sub(r"\s*[#*]+\s*$", "", name).strip()
    for pattern, canonical in APP_PATTERNS:
        if re.search(pattern, name, re.I):
            return canonical
    return name


def npci_apps() -> None:
    totals: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for month, results in npci_tab("upi-apps"):
        for r in results:
            key = (month, app_key(r["application_name"]))
            totals[key][0] += number(r["total_volume_mn"])
            totals[key][1] += number(r["total_value_cr"])
    rows = [[m, a, round(v, 2), round(c, 2)] for (m, a), (v, c) in totals.items()]
    write_by_year("npci_upi_apps.csv", ["month", "app", "volume_mn", "value_crore"], rows)


def npci_member_banks() -> None:
    rows = []
    for month, results in npci_tab("top-50-mem-vol-val"):
        for r in results:
            name = "TOTAL" if r.get("is_total") else bank_key(r["bank_name"])
            rows.append([month, name, number(r["volume_mn"]), number(r["value_cr"])])
    write_by_year("npci_upi_member_banks.csv", ["month", "bank", "volume_mn", "value_crore"], rows)


def npci_states() -> None:
    rows = []
    for month, results in npci_tab("statewise-statistic"):
        totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        has_districts = any(r.get("district") for r in results)
        for r in results:
            if has_districts and r.get("district"):
                continue  # district releases also carry one "<STATE> Total" row per state
            state = state_key(r["state_union_territory"])
            if state in ("TOTAL", "GRAND TOTAL"):
                continue
            totals[state][0] += number(r["volume_in_mn"])
            totals[state][1] += number(r["value_in_cr"])
        rows += [[month, s, round(v, 2), round(c, 2)] for s, (v, c) in totals.items()]
    write_by_year("npci_upi_states.csv", ["month", "state", "volume_mn", "value_crore"], rows)


def phonepe_pulse() -> None:
    rows = []
    root = ARCHIVE / "phonepe_pulse" / "aggregated_transaction_state"
    for path in sorted(root.glob("*/*/*.json")):
        state, year, quarter = state_key(path.parts[-3]), path.parts[-2], path.stem
        counts = {"P2P": 0.0, "Utility": 0.0, "Merchant": 0.0}
        for item in json.loads(path.read_text(encoding="utf-8"))["data"]["transactionData"] or []:
            kind = item["name"]
            total = sum(p.get("count", 0) for p in item["paymentInstruments"])
            if kind == "Peer-to-peer payments" or kind == "P2P":
                counts["P2P"] += total
            elif kind in ("Utility", "Recharge & bill payments"):
                counts["Utility"] += total
            else:
                counts["Merchant"] += total
        rows.append([f"{year}-Q{quarter}", state, counts["P2P"], counts["Utility"], counts["Merchant"]])
    write_by_year("phonepe_pulse_states.csv", ["quarter", "state", "p2p_count", "utility_count",
                                               "merchant_count"], rows)


def main() -> None:
    rbi_bankwise()
    npci_apps()
    npci_member_banks()
    npci_states()
    phonepe_pulse()


if __name__ == "__main__":
    main()
