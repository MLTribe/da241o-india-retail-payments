"""Build final_analysis_data/: only the data the final analysis needs, one folder per agency.

Each file keeps just the series named in the Assignment 2 data table (plus a source column),
stacked from the calendar-year files in by_year/ with months written as YYYY-MM. Column names
are unchanged from by_year/ so values can be traced back. monthly_panel.csv is copied from
analysis/output/tp_panel.csv, so run analysis/final_analysis.py first.

Run from the assignment folder:  python3 scripts/build_final_analysis_data.py
"""
from __future__ import annotations

import csv
import hashlib
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BY_YEAR = REPO / "by_year"
OUTPUT = REPO / "analysis" / "output"
DEST = REPO / "final_analysis_data"

# (output file, by_year file, columns kept, contents, units)
SOURCES = [
    ("npci/upi.csv", "npci_upi_product_statistics.csv",
     ["month", "volume_mn", "value_crore", "source_url"],
     "UPI total: number and value of payments", "volume million; value ₹ crore"),
    ("npci/imps.csv", "npci_imps_product_statistics.csv",
     ["month", "volume_mn", "value_crore", "source_url"],
     "IMPS: number and value of payments", "volume million; value ₹ crore"),
    ("npci/upi_p2p_p2m.csv", "npci_upi_p2p_p2m_transactions.csv",
     ["month", "p2p_volume_mn", "p2p_value_crore", "p2m_volume_mn", "p2m_value_crore", "source_url"],
     "UPI split into person-to-person (P2P) and person-to-merchant (P2M)", "volume million; value ₹ crore"),
    ("rbi/cards_psi.csv", "rbi_psi_card_ppi_own_month.csv",
     ["month", "credit_total_volume_lakh", "credit_total_value_crore", "credit_pos_volume_lakh",
      "credit_pos_value_crore", "credit_other_volume_lakh", "credit_other_value_crore",
      "debit_total_volume_lakh", "debit_total_value_crore", "debit_pos_volume_lakh", "debit_pos_value_crore",
      "debit_other_volume_lakh", "debit_other_value_crore", "rbi_release_page_url"],
     "Card payments, Payment System Indicators (in-store = pos, online = other)", "volume lakh; value ₹ crore"),
    ("rbi/cards_bankwise_2014_2022.csv", "rbi_bankwise_card_totals.csv",
     ["month", "credit_cards", "debit_cards", "pos_terminals", "credit_payments_n", "credit_payments_v_crore",
      "debit_payments_n", "debit_payments_v_crore", "source_file"],
     "Card payments and stocks, all-bank totals of RBI bank-wise pages (older layout)",
     "counts in units; value ₹ crore"),
    ("rbi/card_acceptance.csv", "rbi_psi_infrastructure.csv",
     ["month", "credit_cards_outstanding", "debit_cards_outstanding", "pos_terminals", "bharat_qr_codes",
      "upi_qr_codes", "source_file"],
     "Cards issued, card machines (PoS) and QR codes, Payment System Indicators Part III", "lakh"),
    ("mospi/cpi.csv", "mospi_cpi_combined_monthly.csv",
     ["month", "cpi_combined_index_linked_2024", "native_base_year", "source_url"],
     "CPI Combined, all-India general index, linked to base 2024 = 100", "index"),
]

PANEL_FIELDS = {
    "month": "Calendar month (YYYY-MM)",
    "upi_volume_mn": "UPI total payments, million (NPCI)",
    "upi_value_crore": "UPI total value, ₹ crore, nominal",
    "imps_volume_mn": "IMPS payments, million (NPCI)",
    "imps_value_crore": "IMPS value, ₹ crore, nominal",
    "p2m_volume_mn": "UPI person-to-merchant payments, million (NPCI, from Apr 2020)",
    "p2m_value_crore": "UPI person-to-merchant value, ₹ crore, nominal",
    "debit_volume_mn": "Debit-card payments (in-store + online), million. RBI PSI from Jun 2021; before that RBI bank-wise totals scaled to PSI",
    "debit_value_crore": "Debit-card payment value, ₹ crore, nominal (same join as volume)",
    "credit_volume_mn": "Credit-card payments (in-store + online), million (same join as debit)",
    "credit_value_crore": "Credit-card payment value, ₹ crore, nominal",
    "share_upi_ui_pct": "UPI share of UPI + IMPS payments, %",
    "share_imps_ui_pct": "IMPS share of UPI + IMPS payments, %",
    "share_p2m_pc_pct": "UPI P2M share of P2M + debit + credit payments, %",
    "share_debit_pc_pct": "Debit share of P2M + debit + credit payments, %",
    "share_credit_pc_pct": "Credit share of P2M + debit + credit payments, %",
    "share_upi_uc_pct": "UPI total share of UPI + debit + credit payments, %",
    "share_debit_uc_pct": "Debit share of UPI + debit + credit payments, %",
    "share_credit_uc_pct": "Credit share of UPI + debit + credit payments, %",
    "share_debit_cc_pct": "Debit share of card payments (debit + credit), %",
    "share_credit_cc_pct": "Credit share of card payments (debit + credit), %",
    "credit_cards_lakh": "Credit cards outstanding, lakh (bank-wise totals before Jun 2021, PSI after)",
    "debit_cards_lakh": "Debit cards outstanding, lakh (same sources)",
    "pos_terminals_lakh": "PoS terminals, lakh (same sources)",
    "debit_atm_withdrawals_mn": "Debit-card ATM cash withdrawals, million (cash context; RBI bank-wise statistics)",
    "diwali": "1 in the Diwali month",
    "covid": "1 in a COVID wave month (Mar–Jun 2020, Apr–Jun 2021)",
}
PANEL_PATTERNS = {
    "_real_value_crore": "value, ₹ crore at March 2026 prices (deflated by CPI Combined)",
    "_real_ticket_rs": "average payment, ₹ at March 2026 prices (real value ÷ volume)",
}

EVENTS = [
    ("2016-11", "2017-03", "Demonetisation", "Months left out of all tests and starting points"),
    ("2018-08", "", "NPCI stops counting UPI transfers between a person's own accounts", "Level dummy for UPI series"),
    ("2019-11", "", "RBI card data definition change (bank-wise pages)", "Level dummy for card series"),
    ("2020-01", "", "Zero merchant fee (MDR) on UPI and RuPay debit", "Tested as a break date; slope change in segmented OLS"),
    ("2020-03", "2020-06", "COVID-19 first wave", "One dummy per month (covid = 1)"),
    ("2020-04", "", "NPCI P2P / P2M split begins", "Merchant-payment tests start here"),
    ("2021-04", "2021-06", "COVID-19 second wave", "One dummy per month (covid = 1)"),
    ("2021-06", "", "RBI card reclassification; PSI series start", "Old and new card data joined here; level dummy"),
    ("2022-03", "", "RBI bank-wise page layout change", "Level dummy for card series"),
    ("2022-09", "", "RuPay credit cards usable on UPI (launched 20 Sep 2022)", "A2 dummy from Oct 2022; sensitivity removes 0/5/10% of credit volume from Sep 2022"),
    ("2023-04", "", "Interchange on wallet (PPI) UPI merchant payments above ₹2,000", "A2 control dummy"),
]


def stack(filename: str, keep: list[str]) -> list[dict[str, str]]:
    rows = []
    for path in sorted(BY_YEAR.glob(f"*/{filename}")):
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            missing = set(keep) - set(reader.fieldnames)
            if missing:
                raise ValueError(f"{path}: missing {sorted(missing)}")
            for row in reader:
                rows.append({k: row["month"].strip()[:7] if k == "month" else row[k] for k in keep})
    if not rows:
        raise FileNotFoundError(filename)
    return sorted(rows, key=lambda r: r["month"])


def write(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def describe(field: str) -> str:
    if field in PANEL_FIELDS:
        return PANEL_FIELDS[field]
    for suffix, text in PANEL_PATTERNS.items():
        if field.endswith(suffix):
            return f"{field[: -len(suffix)].upper()} {text}"
    raise KeyError(f"no description for panel column {field}")


def main() -> None:
    for old in ("sources", "npci", "rbi", "mospi"):
        shutil.rmtree(DEST / old, ignore_errors=True)
    manifest = []

    def record(rel, contents, units, rows, months=True):
        path = DEST / rel
        span = sorted({r["month"] for r in rows}) if months else ["", ""]
        manifest.append({"file": rel, "contents": contents, "units": units, "first_month": span[0],
                         "last_month": span[-1], "rows": len(rows),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    for rel, filename, keep, contents, units in SOURCES:
        rows = stack(filename, keep)
        write(DEST / rel, keep, rows)
        record(rel, contents, units, rows)

    with (OUTPUT / "monthly_five_systems.csv").open(newline="", encoding="utf-8") as stream:
        rows = [{"month": r["period_start"][:7], "system": r["system"], "volume_mn": r["volume_mn"],
                 "value_crore": r["value_cr"]} for r in csv.DictReader(stream) if r["system"] in ("UPI", "IMPS")]
    write(DEST / "rbi/daily_based_upi_imps.csv", ["month", "system", "volume_mn", "value_crore"], rows)
    record("rbi/daily_based_upi_imps.csv", "UPI and IMPS monthly totals summed from RBI's daily payment data",
           "volume million; value ₹ crore", rows)

    with (OUTPUT / "tp_panel.csv").open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields, rows = list(reader.fieldnames), list(reader)
    write(DEST / "monthly_panel.csv", fields, rows)
    record("monthly_panel.csv", "Analysis-ready monthly panel built from the files above", "see data_dictionary.csv", rows)

    dictionary = [{"column": f, "description": describe(f)} for f in fields]
    write(DEST / "data_dictionary.csv", ["column", "description"], dictionary)
    record("data_dictionary.csv", "Column descriptions for monthly_panel.csv", "", dictionary, months=False)

    events = [dict(zip(("from", "to", "event", "how_handled"), e)) for e in EVENTS]
    write(DEST / "events.csv", ["from", "to", "event", "how_handled"], events)
    record("events.csv", "Dated breaks, policy changes and COVID waves (A2 Appendix C1 and controls)", "", events,
           months=False)

    write(DEST / "manifest.csv", list(manifest[0]), manifest)
    print(f"Wrote {len(manifest)} files to {DEST.relative_to(REPO)}/ plus manifest.csv")


if __name__ == "__main__":
    main()
