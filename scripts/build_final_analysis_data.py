"""Build final_analysis_data/: only the data the final analysis needs, one folder per agency.

Each file keeps just the series named in the Assignment 2 data table (plus a source column),
stacked from the calendar-year files in by_year/ with months written as YYYY-MM. Column names
are unchanged from by_year/ so values can be traced back. monthly_panel.csv is copied from
analysis/output/tp_panel.csv, so run analysis/final_analysis.py first. daily_panel.csv is built
from analysis/output/daily_five_systems.csv (analysis/daily_trends.py).

Run from the assignment folder:  python3 scripts/build_final_analysis_data.py
"""
from __future__ import annotations

import csv
import datetime
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

DAILY_SYSTEMS = {"UPI": "upi", "IMPS": "imps", "Credit card": "credit_card", "Debit card": "debit_card"}
DAILY_NAMES = {"upi": "UPI", "imps": "IMPS", "credit_card": "Credit-card (card-network only)",
               "debit_card": "Debit-card (card-network only)"}
# Major festivals on the Government of India central holiday list (dates as encoded in python-holidays
# 0.108, India calendar); national days such as Republic Day are not festivals and are left out.
FESTIVALS = [
    ("2020-03-10", "Holi"), ("2020-05-25", "Eid al-Fitr"), ("2020-08-01", "Eid al-Adha (Bakrid)"),
    ("2020-08-12", "Janmashtami"), ("2020-10-25", "Dussehra"), ("2020-11-14", "Diwali"),
    ("2020-11-30", "Guru Nanak Jayanti"), ("2020-12-25", "Christmas"), ("2021-03-29", "Holi"),
    ("2021-05-14", "Eid al-Fitr"), ("2021-07-21", "Eid al-Adha (Bakrid)"), ("2021-08-30", "Janmashtami"),
    ("2021-10-15", "Dussehra"), ("2021-11-04", "Diwali"), ("2021-11-19", "Guru Nanak Jayanti"),
    ("2021-12-25", "Christmas"), ("2022-03-18", "Holi"), ("2022-05-03", "Eid al-Fitr"),
    ("2022-07-10", "Eid al-Adha (Bakrid)"), ("2022-08-19", "Janmashtami"), ("2022-10-05", "Dussehra"),
    ("2022-10-24", "Diwali"), ("2022-11-08", "Guru Nanak Jayanti"), ("2022-12-25", "Christmas"),
    ("2023-03-08", "Holi"), ("2023-04-22", "Eid al-Fitr"), ("2023-06-29", "Eid al-Adha (Bakrid)"),
    ("2023-09-07", "Janmashtami"), ("2023-10-24", "Dussehra"), ("2023-11-12", "Diwali"),
    ("2023-11-27", "Guru Nanak Jayanti"), ("2023-12-25", "Christmas"), ("2024-03-25", "Holi"),
    ("2024-04-11", "Eid al-Fitr"), ("2024-06-17", "Eid al-Adha (Bakrid)"), ("2024-08-26", "Janmashtami"),
    ("2024-10-12", "Dussehra"), ("2024-10-31", "Diwali"), ("2024-11-15", "Guru Nanak Jayanti"),
    ("2024-12-25", "Christmas"), ("2025-03-14", "Holi"), ("2025-03-31", "Eid al-Fitr"),
    ("2025-06-07", "Eid al-Adha (Bakrid)"), ("2025-08-16", "Janmashtami"), ("2025-10-02", "Dussehra"),
    ("2025-10-20", "Diwali"), ("2025-11-05", "Guru Nanak Jayanti"), ("2025-12-25", "Christmas"),
    ("2026-03-04", "Holi"), ("2026-03-21", "Eid al-Fitr"), ("2026-05-27", "Eid al-Adha (Bakrid)"),
    ("2026-09-04", "Janmashtami"), ("2026-10-20", "Dussehra"), ("2026-11-08", "Diwali"),
    ("2026-11-24", "Guru Nanak Jayanti"), ("2026-12-25", "Christmas"),
]
FESTIVAL_DAYS = {}
for _date, _name in FESTIVALS:
    FESTIVAL_DAYS[_date] = f"{FESTIVAL_DAYS[_date]}; {_name}" if _date in FESTIVAL_DAYS else _name
FESTIVAL_MONTHS = {}
for _date, _name in FESTIVALS:
    FESTIVAL_MONTHS.setdefault(_date[:7], []).append(_name)
COVID_DAYS = ("2021-04-01", "2021-06-30")
DAILY_FIELDS = {
    "date": "Calendar date (YYYY-MM-DD)",
    "month": "Calendar month (YYYY-MM), to join with monthly_panel.csv",
    "weekday": "Day of the week (Mon … Sun)",
    "weekend": "1 on Saturday or Sunday",
    "share_upi_ui_pct": "UPI share of UPI + IMPS payments that day, %",
    "share_imps_ui_pct": "IMPS share of UPI + IMPS payments that day, %",
    "share_debit_cc_pct": "Debit share of card-network card payments that day, %",
    "share_credit_cc_pct": "Credit share of card-network card payments that day, %",
    "festival_day": "1 on a major festival (Holi, Eid al-Fitr, Eid al-Adha, Janmashtami, Dussehra, Diwali, Guru Nanak Jayanti, Christmas; see festivals.csv)",
    "festival_name": "Name of the festival on that day, blank otherwise",
    "festival_month": "1 if any of those festivals falls in the calendar month",
    "festival_month_names": "Festivals in that calendar month, separated by '; '",
    "covid": "1 in the second COVID wave (1 Apr – 30 Jun 2021); the daily data start after the first wave",
}
DAILY_PATTERNS = {
    "_volume_mn": "payments that day, million (RBI daily data)",
    "_value_crore": "value that day, ₹ crore, nominal",
    "_ticket_rs": "average payment that day, ₹, nominal (value ÷ volume; blank if no payments settled)",
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


def describe(field: str, fixed=PANEL_FIELDS, patterns=PANEL_PATTERNS, names=None) -> str:
    if field in fixed:
        return fixed[field]
    for suffix, text in patterns.items():
        if field.endswith(suffix):
            stem = field[: -len(suffix)]
            return f"{names[stem] if names else stem.upper()} {text}"
    raise KeyError(f"no description for panel column {field}")


def daily_panel() -> tuple[list[str], list[dict[str, object]]]:
    days: dict[str, dict[str, object]] = {}
    with (OUTPUT / "daily_five_systems.csv").open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            key = DAILY_SYSTEMS.get(row["system"])
            if key:
                day = days.setdefault(row["date"], {})
                day[f"{key}_volume_mn"] = float(row["volume_mn"])
                day[f"{key}_value_crore"] = float(row["value_cr"])
    fields = ["date", "month", "weekday", "weekend"]
    for key in DAILY_SYSTEMS.values():
        fields += [f"{key}_volume_mn", f"{key}_value_crore", f"{key}_ticket_rs"]
    fields += ["share_upi_ui_pct", "share_imps_ui_pct", "share_debit_cc_pct", "share_credit_cc_pct",
               "festival_day", "festival_name", "festival_month", "festival_month_names", "covid"]
    share = lambda a, b: round(100 * a / (a + b), 4) if a + b else ""  # noqa: E731
    rows = []
    for date in sorted(days):
        d = days[date]
        if len(d) != 2 * len(DAILY_SYSTEMS):
            raise ValueError(f"{date}: not every system is present")
        weekday = datetime.date.fromisoformat(date).strftime("%a")
        row = {"date": date, "month": date[:7], "weekday": weekday, "weekend": int(weekday in ("Sat", "Sun"))}
        for key in DAILY_SYSTEMS.values():
            n, v = d[f"{key}_volume_mn"], d[f"{key}_value_crore"]
            row.update({f"{key}_volume_mn": n, f"{key}_value_crore": v,
                        f"{key}_ticket_rs": round(v * 10 / n, 2) if n else ""})
        row.update({"share_upi_ui_pct": share(d["upi_volume_mn"], d["imps_volume_mn"]),
                    "share_imps_ui_pct": share(d["imps_volume_mn"], d["upi_volume_mn"]),
                    "share_debit_cc_pct": share(d["debit_card_volume_mn"], d["credit_card_volume_mn"]),
                    "share_credit_cc_pct": share(d["credit_card_volume_mn"], d["debit_card_volume_mn"]),
                    "festival_day": int(date in FESTIVAL_DAYS), "festival_name": FESTIVAL_DAYS.get(date, ""),
                    "festival_month": int(date[:7] in FESTIVAL_MONTHS),
                    "festival_month_names": "; ".join(FESTIVAL_MONTHS.get(date[:7], [])),
                    "covid": int(COVID_DAYS[0] <= date <= COVID_DAYS[1])})
        rows.append(row)
    return fields, rows


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

    dictionary = [{"file": "monthly_panel.csv", "column": f, "description": describe(f)} for f in fields]

    daily_fields, daily_rows = daily_panel()
    write(DEST / "daily_panel.csv", daily_fields, daily_rows)
    record("daily_panel.csv", "Daily panel from RBI's daily payment data: UPI, IMPS and card-network card payments",
           "see data_dictionary.csv", daily_rows)
    dictionary += [{"file": "daily_panel.csv", "column": f,
                    "description": describe(f, DAILY_FIELDS, DAILY_PATTERNS, DAILY_NAMES)} for f in daily_fields]

    write(DEST / "data_dictionary.csv", ["file", "column", "description"], dictionary)
    record("data_dictionary.csv", "Column descriptions for monthly_panel.csv and daily_panel.csv", "", dictionary,
           months=False)

    festivals = [{"date": d, "month": d[:7], "festival": n} for d, n in FESTIVALS]
    write(DEST / "festivals.csv", ["date", "month", "festival"], festivals)
    record("festivals.csv", "Major festival dates used for the festival flags in daily_panel.csv "
           "(Government of India central holiday list)", "", festivals)

    events = [dict(zip(("from", "to", "event", "how_handled"), e)) for e in EVENTS]
    write(DEST / "events.csv", ["from", "to", "event", "how_handled"], events)
    record("events.csv", "Dated breaks, policy changes and COVID waves (A2 Appendix C1 and controls)", "", events,
           months=False)

    write(DEST / "manifest.csv", list(manifest[0]), manifest)
    print(f"Wrote {len(manifest)} files to {DEST.relative_to(REPO)}/ plus manifest.csv")


if __name__ == "__main__":
    main()
