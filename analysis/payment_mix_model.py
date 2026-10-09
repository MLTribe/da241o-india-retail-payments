#!/usr/bin/env python3
"""Describe payment-rail expansion, contraction, and changing activity shares.

Run from any directory with: python3 analysis/payment_mix_model.py
Inputs are the annual CSV extracts in ../by_year. No third-party package is needed.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
INPUT = REPO / "by_year"
OUTPUT = Path(__file__).resolve().parent / "output"
FIGURES = OUTPUT / "figures"
DOCS_DATA = REPO / "docs" / "explore-data.js"
END_MONTH = "2026-03"
TRANSFER_START = "2016-04"
BASE_MONTH = "2026-03"
CORE_BASKETS = ("transfer", "merchant")
BASKET_LABELS = {
    "transfer": "UPI total vs IMPS",
    "merchant": "Merchant payments: UPI P2M vs domestic cards",
    "merchant_channel": "Merchant channel: UPI P2M vs card PoS and card online",
    "upi_use": "Within UPI: P2P vs P2M",
    "context_retail": "Context: wider retail digital rails",
    "cash_context": "Context: ATM cash withdrawals vs UPI P2M",
    "wholesale_context": "Context: RTGS (levels only)",
}
# Descriptive segment boundaries for trend slopes; not estimated break dates.
TREND_BREAKS = {"transfer": ["2020-04"]}


def month_key(raw: str) -> str:
    month = raw[:7]
    if len(month) != 7 or month[4] != "-" or not 1 <= int(month[5:7]) <= 12:
        raise ValueError(f"Invalid month: {raw!r}")
    if len(raw) not in (7, 10) or (len(raw) == 10 and raw[7:] != "-01"):
        raise ValueError(f"Unexpected month format: {raw!r}")
    return month


def shift_month(month: str, n: int) -> str:
    year, mm = map(int, month.split("-"))
    index = year * 12 + mm - 1 + n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_range(start: str, end: str) -> list[str]:
    months = []
    current = start
    while current <= end:
        months.append(current)
        current = shift_month(current, 1)
    return months


def read_series(filename: str, start: str | None,
                optional: bool = False) -> dict[str, dict[str, str]]:
    """Read one series; start=None means 'from its first observation'."""
    rows: dict[str, dict[str, str]] = {}
    for path in sorted(INPUT.glob(f"*/{filename}")):
        with path.open(newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                month = month_key(row["month"])
                if month[:4] != path.parent.name:
                    raise ValueError(f"Observation year disagrees with folder: {path}: {month}")
                if month in rows:
                    raise ValueError(f"Duplicate {filename} observation: {month}")
                rows[month] = row
    if not rows:
        if optional:
            return {}
        raise ValueError(f"No {filename} observations found")
    expected = set(month_range(start or min(rows), END_MONTH))
    absent = sorted(expected - rows.keys())
    if absent:
        raise ValueError(f"Missing {filename} months: {absent}")
    return rows


def numeric(row: dict[str, str], field: str, allow_zero: bool = False) -> float:
    value = float(row[field])
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"Invalid {field} in {row['month']}: {value}")
    return value


def near(actual: float, expected: float, tolerance: float, label: str, month: str) -> float:
    difference = abs(actual - expected)
    if difference > tolerance:
        raise ValueError(f"{label} identity fails in {month}: difference {difference:g}")
    return difference


def reconcile(
    upi: dict[str, dict[str, str]],
    split: dict[str, dict[str, str]],
    rbi: dict[str, dict[str, str]],
    cpi: dict[str, dict[str, str]],
    merchant_start: str,
    infra: dict[str, dict[str, str]],
) -> tuple[dict[str, float], list[dict[str, object]]]:
    maxima: dict[str, float] = defaultdict(float)
    cross_source_discrepancies: list[dict[str, object]] = []
    for month, row in infra.items():
        for field, raw in row.items():
            if field not in ("month", "unit", "source_file") and float(raw) < 0:
                raise ValueError(f"Negative infrastructure count {field} in {month}")
    for month in month_range(merchant_start, END_MONTH):
        u, s, r, p = upi[month], split[month], rbi[month], cpi[month]
        product_volume = numeric(u, "volume_mn")
        ecosystem_volume = numeric(s, "total_volume_mn")
        product_value = numeric(u, "value_crore")
        ecosystem_value = numeric(s, "total_value_crore")
        volume_difference = ecosystem_volume - product_volume
        value_difference = ecosystem_value - product_value
        maxima["UPI ecosystem vs product volume (mn)"] = max(
            maxima["UPI ecosystem vs product volume (mn)"], abs(volume_difference))
        maxima["UPI ecosystem vs product value (crore)"] = max(
            maxima["UPI ecosystem vs product value (crore)"], abs(value_difference))
        if abs(volume_difference) > 0.11 or abs(value_difference) > 0.11:
            cross_source_discrepancies.append({
                "month": month,
                "ecosystem_minus_product_volume_mn": round(volume_difference, 6),
                "ecosystem_minus_product_value_crore": round(value_difference, 6),
            })
        checks = {
            "P2P + P2M volume vs ecosystem total (mn)": (
                numeric(s, "p2p_volume_mn") + numeric(s, "p2m_volume_mn"),
                numeric(s, "total_volume_mn"), 0.11
            ),
            "P2P + P2M value vs ecosystem total (crore)": (
                numeric(s, "p2p_value_crore") + numeric(s, "p2m_value_crore"),
                numeric(s, "total_value_crore"), 0.11
            ),
            "credit + debit volume vs card total (lakh)": (
                numeric(r, "credit_total_volume_lakh") + numeric(r, "debit_total_volume_lakh"),
                numeric(r, "card_total_volume_lakh"), 0.11
            ),
            "credit + debit value vs card total (crore)": (
                numeric(r, "credit_total_value_crore") + numeric(r, "debit_total_value_crore"),
                numeric(r, "card_total_value_crore"), 1.1
            ),
        }
        for unit, tolerance in (("volume_lakh", 0.11), ("value_crore", 1.1)):
            label = "volume (lakh)" if unit == "volume_lakh" else "value (crore)"
            checks[f"PPI wallets + cards {label}"] = (
                numeric(r, f"ppi_wallets_{unit}") + numeric(r, f"ppi_cards_{unit}"),
                numeric(r, f"ppi_total_{unit}"), tolerance
            )
            checks[f"PPI card PoS + Other {label}"] = (
                numeric(r, f"ppi_card_pos_{unit}", allow_zero=True)
                + numeric(r, f"ppi_card_other_{unit}", allow_zero=True),
                numeric(r, f"ppi_cards_{unit}"), tolerance
            )
        for rail in ("credit", "debit"):
            checks[f"{rail} PoS + Other volume (lakh)"] = (
                numeric(r, f"{rail}_pos_volume_lakh")
                + numeric(r, f"{rail}_other_volume_lakh"),
                numeric(r, f"{rail}_total_volume_lakh"), 0.11
            )
            checks[f"{rail} PoS + Other value (crore)"] = (
                numeric(r, f"{rail}_pos_value_crore")
                + numeric(r, f"{rail}_other_value_crore"),
                numeric(r, f"{rail}_total_value_crore"), 1.1
            )
        for label, (actual, expected, tolerance) in checks.items():
            maxima[label] = max(maxima[label], near(actual, expected, tolerance, label, month))
        linked = numeric(p, "cpi_combined_index_linked_2024")
        recomputed = numeric(p, "cpi_combined_index_native") * numeric(
            p, "link_factor_to_2024"
        )
        maxima["CPI linked-index rounding"] = max(
            maxima["CPI linked-index rounding"],
            near(linked, recomputed, 0.00001, "CPI link", month),
        )
    return dict(sorted(maxima.items())), cross_source_discrepancies


def rail_row(month: str, basket: str, rail: str, volume: float,
             value: float, source_url: str, cpi_now: float | None,
             cpi_base: float) -> dict[str, object]:
    if volume < 0 or value < 0 or ((volume == 0) != (value == 0)):
        raise ValueError(f"Invalid volume/value pair for {rail}, {month}")
    real_value = value * cpi_base / cpi_now if cpi_now is not None else None
    return {
        "month": month,
        "basket": basket,
        "rail": rail,
        "volume_mn": volume,
        "value_nominal_crore": value,
        "value_real_mar2026_crore": real_value,
        "average_ticket_nominal_rs": value * 10 / volume if volume else None,
        "average_ticket_real_mar2026_rs": (
            real_value * 10 / volume if real_value is not None and volume else None
        ),
        "source_url": source_url,
    }


def covers(series: dict[str, dict[str, str]], start: str) -> bool:
    return bool(series) and all(m in series for m in month_range(start, END_MONTH))


def rbi_rail(row: dict[str, str], stem: str) -> tuple[float, float]:
    """RBI volume in lakh -> millions, value stays in crore."""
    return (numeric(row, f"{stem}_volume_lakh", allow_zero=True) / 10,
            numeric(row, f"{stem}_value_crore", allow_zero=True))


def build_monthly(
    upi: dict[str, dict[str, str]], imps: dict[str, dict[str, str]],
    split: dict[str, dict[str, str]], rbi: dict[str, dict[str, str]],
    cpi: dict[str, dict[str, str]], merchant_start: str,
    other: dict[str, dict[str, str]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    cpi_base = numeric(cpi[BASE_MONTH], "cpi_combined_index_linked_2024")
    # Optional rails join a basket only if they cover its whole window, so
    # basket composition never changes from month to month.
    has_other = covers(other, merchant_start)
    for month in month_range(TRANSFER_START, END_MONTH):
        current_cpi = (numeric(cpi[month], "cpi_combined_index_linked_2024")
                       if month in cpi else None)
        for rail, source, volume_col in (
            ("UPI total", upi[month], "volume_mn"),
            ("IMPS", imps[month], "volume_mn"),
        ):
            rows.append(rail_row(
                month, "transfer", rail, numeric(source, volume_col, allow_zero=True),
                numeric(source, "value_crore", allow_zero=True), source["source_url"],
                current_cpi, cpi_base,
            ))
        if month < merchant_start:
            continue
        s, r = split[month], rbi[month]
        npci_url, rbi_url = s["source_url"], r["rbi_release_page_url"]
        p2m = (numeric(s, "p2m_volume_mn"), numeric(s, "p2m_value_crore"))
        baskets: dict[str, list[tuple[str, tuple[float, float], str]]] = {
            "merchant": [
                ("UPI P2M", p2m, npci_url),
                ("credit card", rbi_rail(r, "credit_total"), rbi_url),
                ("debit card", rbi_rail(r, "debit_total"), rbi_url),
            ],
            "merchant_channel": [
                ("UPI P2M", p2m, npci_url),
                ("credit card PoS", rbi_rail(r, "credit_pos"), rbi_url),
                ("credit card online/other", rbi_rail(r, "credit_other"), rbi_url),
                ("debit card PoS", rbi_rail(r, "debit_pos"), rbi_url),
                ("debit card online/other", rbi_rail(r, "debit_other"), rbi_url),
            ],
            "upi_use": [
                ("UPI P2P", (numeric(s, "p2p_volume_mn"), numeric(s, "p2p_value_crore")),
                 npci_url),
                ("UPI P2M", p2m, npci_url),
            ],
            "context_retail": [
                ("UPI total", (numeric(upi[month], "volume_mn"),
                               numeric(upi[month], "value_crore")), upi[month]["source_url"]),
                ("IMPS", (numeric(imps[month], "volume_mn"),
                          numeric(imps[month], "value_crore")), imps[month]["source_url"]),
                ("cards (credit + debit)", rbi_rail(r, "card_total"), rbi_url),
                ("PPI (wallets + cards)", rbi_rail(r, "ppi_total"), rbi_url),
            ],
        }
        if has_other:
            o = other[month]
            o_url = f"source_archive/rbi/xlsx/{o['source_file']}"
            baskets["context_retail"] += [
                ("NEFT", rbi_rail(o, "neft"), o_url),
                ("AePS fund transfer", rbi_rail(o, "aeps_fund_transfer"), o_url),
                ("NETC", rbi_rail(o, "netc"), o_url),
            ]
            baskets["cash_context"] = [
                ("ATM cash withdrawal", rbi_rail(o, "atm_cash_withdrawal"), o_url),
                ("UPI P2M", p2m, npci_url),
            ]
            baskets["wholesale_context"] = [("RTGS", rbi_rail(o, "rtgs"), o_url)]
        for basket, rails in baskets.items():
            for rail, (volume, value), url in rails:
                rows.append(rail_row(month, basket, rail, volume, value,
                                     url, current_cpi, cpi_base))

    baskets: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        baskets[(str(row["basket"]), str(row["month"]))].append(row)
    for group in baskets.values():
        volume_total = sum(float(row["volume_mn"]) for row in group)
        value_total = sum(float(row["value_nominal_crore"]) for row in group)
        for row in group:
            row["basket_volume_mn"] = volume_total
            row["volume_share_pct"] = 100 * float(row["volume_mn"]) / volume_total
            row["value_share_pct"] = 100 * float(row["value_nominal_crore"]) / value_total
    lookup = {(str(row["basket"]), str(row["rail"]), str(row["month"])): row
              for row in rows}
    for row in rows:
        prior = lookup.get((str(row["basket"]), str(row["rail"]),
                            shift_month(str(row["month"]), -12)))
        row["volume_yoy_pct"] = 100 * (
            float(row["volume_mn"]) / float(prior["volume_mn"]) - 1
        ) if prior and float(prior["volume_mn"]) > 0 else None
        row["volume_share_yoy_pp"] = (
            float(row["volume_share_pct"]) - float(prior["volume_share_pct"])
        ) if prior else None
        if prior and row["value_real_mar2026_crore"] is not None \
                and prior["value_real_mar2026_crore"] is not None \
                and float(prior["value_real_mar2026_crore"]) > 0:
            row["real_value_yoy_pct"] = 100 * (
                float(row["value_real_mar2026_crore"])
                / float(prior["value_real_mar2026_crore"]) - 1
            )
        else:
            row["real_value_yoy_pct"] = None
        row["volume_pattern_yoy"] = classify(
            row["volume_yoy_pct"], row["volume_share_yoy_pp"]
        ) if row["volume_yoy_pct"] is not None else None
    add_rolling(rows, lookup)
    return rows


def add_rolling(rows: list[dict[str, object]],
                lookup: dict[tuple[str, str, str], dict[str, object]]) -> None:
    """Trailing 12-month sums and shares; these remove the monthly seasonal pattern."""
    for row in rows:
        window = [lookup.get((str(row["basket"]), str(row["rail"]),
                              shift_month(str(row["month"]), -lag))) for lag in range(12)]
        if all(item is not None for item in window):
            row["rolling12_volume_mn"] = sum(float(i["volume_mn"]) for i in window)
            row["rolling12_value_nominal_crore"] = sum(
                float(i["value_nominal_crore"]) for i in window)
        else:
            row["rolling12_volume_mn"] = row["rolling12_value_nominal_crore"] = None
    totals: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        key = (str(row["basket"]), str(row["month"]))
        total = totals.setdefault(key, [0.0, 0.0, 1.0])
        if row["rolling12_volume_mn"] is None:
            total[2] = 0.0
        else:
            total[0] += float(row["rolling12_volume_mn"])
            total[1] += float(row["rolling12_value_nominal_crore"])
    for row in rows:
        vol, val, complete = totals[(str(row["basket"]), str(row["month"]))]
        ok = complete and row["rolling12_volume_mn"] is not None
        row["rolling12_volume_share_pct"] = (
            100 * float(row["rolling12_volume_mn"]) / vol if ok and vol > 0 else None)
        row["rolling12_value_share_pct"] = (
            100 * float(row["rolling12_value_nominal_crore"]) / val if ok and val > 0 else None)


def classify(level_growth_pct: object, share_change_pp: object) -> str:
    growth = float(level_growth_pct)
    share = float(share_change_pp)
    # Source precision makes tiny changes indistinguishable from zero.
    if abs(growth) < 0.005 or abs(share) < 0.005:
        return "approximately flat"
    if growth < 0 and share < 0:
        return "absolute contraction"
    if growth > 0 and share < 0:
        return "relative share loss"
    if growth > 0 and share > 0:
        return "co-expansion"
    return "rail fell, share rose"


def fiscal_year(month: str) -> str:
    year, mm = map(int, month.split("-"))
    start = year if mm >= 4 else year - 1
    return f"FY{start}-{str(start + 1)[-2:]}"


def annualize(monthly: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in monthly:
        groups[(str(row["basket"]), fiscal_year(str(row["month"])),
                str(row["rail"]))].append(row)
    annual: list[dict[str, object]] = []
    for (basket, fy, rail), rows in sorted(groups.items()):
        if len(rows) != 12:
            continue
        volume = sum(float(row["volume_mn"]) for row in rows)
        value = sum(float(row["value_nominal_crore"]) for row in rows)
        real_items = [row["value_real_mar2026_crore"] for row in rows]
        real_value = sum(float(item) for item in real_items) if all(
            item is not None for item in real_items
        ) else None
        annual.append({
            "basket": basket, "fiscal_year": fy, "rail": rail,
            "months": 12, "volume_mn": volume,
            "value_nominal_crore": value,
            "value_real_mar2026_crore": real_value,
            "average_ticket_nominal_rs": value * 10 / volume,
            "average_ticket_real_mar2026_rs": (
                real_value * 10 / volume if real_value is not None else None
            ),
        })
    totals: dict[tuple[str, str], tuple[float, float]] = {}
    for row in annual:
        key = (str(row["basket"]), str(row["fiscal_year"]))
        old = totals.get(key, (0.0, 0.0))
        totals[key] = (old[0] + float(row["volume_mn"]),
                       old[1] + float(row["value_nominal_crore"]))
    for row in annual:
        total_vol, total_val = totals[(str(row["basket"]),
                                       str(row["fiscal_year"]))]
        row["volume_share_pct"] = 100 * float(row["volume_mn"]) / total_vol
        row["value_share_pct"] = 100 * float(row["value_nominal_crore"]) / total_val
    return annual


def compare_years(annual: list[dict[str, object]], basket: str,
                  first: str, last: str) -> list[dict[str, object]]:
    lookup = {(row["basket"], row["fiscal_year"], row["rail"]): row
              for row in annual}
    rails = [str(row["rail"]) for row in annual
             if row["basket"] == basket and row["fiscal_year"] == last]
    comparisons = []
    for rail in rails:
        if (basket, first, rail) not in lookup:
            continue
        before = lookup[(basket, first, rail)]
        after = lookup[(basket, last, rail)]
        volume_growth = 100 * (float(after["volume_mn"]) /
                               float(before["volume_mn"]) - 1)
        share_change = float(after["volume_share_pct"]) - float(before["volume_share_pct"])
        real_before = before["value_real_mar2026_crore"]
        real_after = after["value_real_mar2026_crore"]
        comparisons.append({
            "basket": basket, "rail": rail, "first_year": first, "last_year": last,
            "volume_growth_pct": volume_growth,
            "volume_share_change_pp": share_change,
            "value_share_change_pp": (
                float(after["value_share_pct"]) - float(before["value_share_pct"])
            ),
            "nominal_value_growth_pct": 100 * (
                float(after["value_nominal_crore"])
                / float(before["value_nominal_crore"]) - 1
            ),
            "real_value_growth_pct": 100 * (float(real_after) / float(real_before) - 1)
                if real_after is not None and real_before is not None else None,
            "real_ticket_growth_pct": 100 * (
                float(after["average_ticket_real_mar2026_rs"])
                / float(before["average_ticket_real_mar2026_rs"]) - 1
            ) if real_after is not None and real_before is not None else None,
            "pattern": classify(volume_growth, share_change),
        })
    return comparisons


INFRA_FIELDS = [
    "month", "credit_cards_outstanding_lakh", "debit_cards_outstanding_lakh",
    "pos_terminals_lakh", "bharat_qr_codes_lakh", "upi_qr_codes_lakh", "atms_lakh",
    "card_pos_txn_per_pos_terminal", "upi_p2m_txn_per_upi_qr",
    "credit_txn_per_credit_card", "debit_txn_per_debit_card",
]


def build_infrastructure(infra: dict[str, dict[str, str]],
                         split: dict[str, dict[str, str]],
                         rbi: dict[str, dict[str, str]]) -> list[dict[str, object]]:
    """Acceptance points, cards in force, and monthly use per point or card.

    All RBI infrastructure counts are in lakh, as are RBI card volumes, so the
    lakh units cancel in each ratio; P2M millions are converted to lakh (x10).
    """
    panel = []
    for month in sorted(infra):
        if month not in rbi or month not in split:
            continue
        i, r = infra[month], rbi[month]

        def ratio(numerator: float, field: str) -> float | None:
            denominator = float(i[field])
            return numerator / denominator if denominator > 0 else None

        panel.append({
            "month": month,
            "credit_cards_outstanding_lakh": float(i["credit_cards_outstanding"]),
            "debit_cards_outstanding_lakh": float(i["debit_cards_outstanding"]),
            "pos_terminals_lakh": float(i["pos_terminals"]),
            "bharat_qr_codes_lakh": float(i["bharat_qr_codes"]),
            "upi_qr_codes_lakh": float(i["upi_qr_codes"]),
            "atms_lakh": float(i["atms"]),
            "card_pos_txn_per_pos_terminal": ratio(
                numeric(r, "credit_pos_volume_lakh") + numeric(r, "debit_pos_volume_lakh"),
                "pos_terminals"),
            "upi_p2m_txn_per_upi_qr": ratio(
                numeric(split[month], "p2m_volume_mn") * 10, "upi_qr_codes"),
            "credit_txn_per_credit_card": ratio(
                numeric(r, "credit_total_volume_lakh"), "credit_cards_outstanding"),
            "debit_txn_per_debit_card": ratio(
                numeric(r, "debit_total_volume_lakh"), "debit_cards_outstanding"),
        })
    return panel


def ols_slope(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Least-squares slope and R-squared of y on x."""
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    syy = sum((y - mean_y) ** 2 for _, y in points)
    slope = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 1.0
    return slope, r2


def month_index(month: str) -> int:
    year, mm = map(int, month.split("-"))
    return year * 12 + mm - 1


def trend_growth(series: list[tuple[str, float]]) -> tuple[float, float] | None:
    """Annualised log-linear trend growth (%) and R-squared; needs 12+ positive months."""
    points = [(float(month_index(m)), math.log(v)) for m, v in series if v > 0]
    if len(points) < 12:
        return None
    slope, r2 = ols_slope(points)
    return 100 * (math.exp(12 * slope) - 1), r2


def seasonal_index(series: list[tuple[str, float]]) -> dict[int, float] | None:
    """Mean ratio to a centred 2x12 moving average by calendar month, rescaled to 100."""
    values = [v for _, v in series]
    if len(values) < 36 or any(v <= 0 for v in values):
        return None
    ratios: dict[int, list[float]] = defaultdict(list)
    for i in range(6, len(values) - 6):
        centred = (0.5 * values[i - 6] + sum(values[i - 5:i + 6]) + 0.5 * values[i + 6]) / 12
        ratios[int(series[i][0][5:7])].append(values[i] / centred)
    if len(ratios) < 12:
        return None
    raw = {mm: sum(items) / len(items) for mm, items in ratios.items()}
    scale = 12 / sum(raw.values())
    return {mm: 100 * value * scale for mm, value in sorted(raw.items())}


def build_timeseries_stats(monthly: list[dict[str, object]]) -> list[dict[str, object]]:
    stats: list[dict[str, object]] = []

    def add(basket: str, rail: str, statistic: str, period: str, value: float,
            unit: str, note: str = "") -> None:
        stats.append({"basket": basket, "rail": rail, "statistic": statistic,
                      "period": period, "value": value, "unit": unit, "note": note})

    by_rail: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in monthly:
        by_rail[(str(row["basket"]), str(row["rail"]))].append(row)
    for (basket, rail), rows in sorted(by_rail.items()):
        rows.sort(key=lambda item: str(item["month"]))
        months = [str(item["month"]) for item in rows]
        volume = [(str(item["month"]), float(item["volume_mn"])) for item in rows]
        real = [(str(item["month"]), float(item["value_real_mar2026_crore"]))
                for item in rows if item["value_real_mar2026_crore"] is not None]
        if len(rows) >= 24:
            first = sum(v for _, v in volume[:12])
            last = sum(v for _, v in volume[-12:])
            years = (len(rows) - 12) / 12
            if first > 0:
                add(basket, rail, "annualised_growth_first12_to_last12_volume",
                    f"{months[0]}..{months[11]} -> {months[-12]}..{months[-1]}",
                    100 * ((last / first) ** (1 / years) - 1), "% per year",
                    "Compound growth between the first and last 12-month totals.")
        segments = [("full window", months[0], months[-1])]
        if len(rows) >= 24:
            segments.append(("last 24 months", months[-24], months[-1]))
        cuts = [months[0]] + [b for b in TREND_BREAKS.get(basket, []) if months[0] < b <= months[-1]]
        for start, stop in zip(cuts, cuts[1:] + [None]):
            end = shift_month(stop, -1) if stop else months[-1]
            if (start, end) != (months[0], months[-1]):
                segments.append((f"before {stop}" if stop else f"from {start}", start, end))
        for label, start, end in segments:
            for measure, series in (("volume", volume), ("real_value", real)):
                window = [(m, v) for m, v in series if start <= m <= end]
                result = trend_growth(window)
                if result is not None:
                    add(basket, rail, f"trend_growth_{measure}", f"{label}: {start}..{end}",
                        result[0], "% per year", f"log-linear OLS, R2={result[1]:.3f}")
        launched = next((i for i, (_, v) in enumerate(volume) if v > 0), len(volume))
        index = seasonal_index(volume[launched:])
        if index:
            for mm, value in index.items():
                add(basket, rail, "seasonal_index_volume", f"month {mm:02d}", value,
                    "index, mean=100", "Ratio to centred 2x12 moving average.")
    by_basket: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in monthly:
        by_basket[(str(row["basket"]), str(row["month"]))].append(row)
    for (basket, month), rows in sorted(by_basket.items()):
        if len(rows) < 2:
            continue
        for measure in ("volume", "value"):
            add(basket, "all rails", f"hhi_{measure}", month,
                sum(float(row[f"{measure}_share_pct"]) ** 2 for row in rows),
                "HHI 0-10000", "Concentration within the analytical basket only.")
    return stats


SERIES_COLOURS = ["#008738", "#d62828", "#b57700", "#1f5f9e", "#7a3fa0", "#4b6b72", "#c2410c"]


def write_svg_chart(path: Path, title: str, y_label: str,
                    series: dict[str, list[tuple[str, float]]]) -> None:
    """Minimal static line chart so figures need no plotting package."""
    from xml.sax.saxutils import escape
    width, height, left, right, top, bottom = 760, 380, 64, 190, 40, 46
    months = sorted({m for points in series.values() for m, _ in points})
    values = [v for points in series.values() for _, v in points]
    if not months or not values:
        return
    low, high = min(0.0, min(values)), max(values)
    high = high if high > low else low + 1
    raw_step = (high - low) / 4
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw_step)
    low, high = math.floor(low / step) * step, math.ceil(high / step) * step
    span_x = max(len(months) - 1, 1)
    x_of = {m: left + (width - left - right) * i / span_x for i, m in enumerate(months)}

    def y_of(value: float) -> float:
        return top + (height - top - bottom) * (1 - (value - low) / (high - low))

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'font-family="Helvetica, Arial, sans-serif" font-size="11">',
             f'<rect width="{width}" height="{height}" fill="#fffefa"/>',
             f'<text x="{left}" y="22" font-size="14" font-weight="700" fill="#152b33">'
             f'{escape(title)}</text>']
    for tick in range(round((high - low) / step) + 1):
        value = low + tick * step
        y = y_of(value)
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" '
                     f'stroke="#dce5df"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" '
                     f'fill="#53656a">{value:,.4g}</text>')
    for month in months:
        if month.endswith("-01"):
            parts.append(f'<text x="{x_of[month]:.1f}" y="{height - bottom + 16}" '
                         f'text-anchor="middle" fill="#53656a">{month[:4]}</text>')
    parts.append(f'<text x="14" y="{(height - bottom + top) / 2:.0f}" fill="#152b33" '
                 f'transform="rotate(-90 14 {(height - bottom + top) / 2:.0f})" '
                 f'text-anchor="middle">{escape(y_label)}</text>')
    for n, (name, points) in enumerate(series.items()):
        colour = SERIES_COLOURS[n % len(SERIES_COLOURS)]
        path_data = " ".join(f"{'M' if i == 0 else 'L'}{x_of[m]:.1f},{y_of(v):.1f}"
                             for i, (m, v) in enumerate(sorted(points)))
        parts.append(f'<path d="{path_data}" fill="none" stroke="{colour}" stroke-width="2.4"/>')
        legend_y = top + 16 * n + 6
        parts.append(f'<rect x="{width - right + 14}" y="{legend_y - 9}" width="11" '
                     f'height="11" fill="{colour}"/>')
        parts.append(f'<text x="{width - right + 30}" y="{legend_y}" fill="#152b33">'
                     f'{escape(name)}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def write_figures(monthly: list[dict[str, object]], infra_panel: list[dict[str, object]],
                  stats: list[dict[str, object]]) -> list[str]:
    FIGURES.mkdir(parents=True, exist_ok=True)
    for old in FIGURES.glob("*.svg"):
        old.unlink()

    def series_for(basket: str, field: str) -> dict[str, list[tuple[str, float]]]:
        out: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for row in monthly:
            if row["basket"] == basket and row.get(field) is not None:
                out[str(row["rail"])].append((str(row["month"]), float(row[field])))
        return dict(out)

    specs = [
        ("merchant_volume_share.svg", "Merchant basket: monthly volume share",
         "Volume share (%)", series_for("merchant", "volume_share_pct")),
        ("merchant_channel_rolling_share.svg",
         "Merchant channel: 12-month rolling volume share", "Volume share (%)",
         series_for("merchant_channel", "rolling12_volume_share_pct")),
        ("upi_use_value_share.svg", "Within UPI: P2P vs P2M value share",
         "Value share (%)", series_for("upi_use", "value_share_pct")),
        ("transfer_volume_share.svg", "UPI total vs IMPS: monthly volume share",
         "Volume share (%)", series_for("transfer", "volume_share_pct")),
        ("merchant_real_ticket.svg", "Merchant basket: real average ticket (Mar 2026 Rs)",
         "Rupees per transaction", series_for("merchant", "average_ticket_real_mar2026_rs")),
        ("context_retail_volume_share.svg", "Context basket: monthly volume share",
         "Volume share (%)", series_for("context_retail", "volume_share_pct")),
    ]
    hhi: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for item in stats:
        if item["statistic"] == "hhi_volume" and item["basket"] in (
                "merchant", "merchant_channel", "context_retail"):
            hhi[str(item["basket"])].append((str(item["period"]), float(item["value"])))
    specs.append(("hhi_volume.svg", "Volume concentration (HHI) by basket",
                  "HHI (0-10000)", dict(hhi)))
    if infra_panel:
        specs.append(("intensity.svg", "Monthly transactions per acceptance point or card",
                      "Transactions per month", {
                          label: [(str(r["month"]), float(r[field])) for r in infra_panel
                                  if r[field] is not None]
                          for label, field in (
                              ("card PoS per PoS terminal", "card_pos_txn_per_pos_terminal"),
                              ("UPI P2M per UPI QR", "upi_p2m_txn_per_upi_qr"),
                              ("credit txns per credit card", "credit_txn_per_credit_card"),
                              ("debit txns per debit card", "debit_txn_per_debit_card"))}))
    written = []
    for filename, title, y_label, series in specs:
        if series:
            write_svg_chart(FIGURES / filename, title, y_label, series)
            written.append(filename)
    return written


def write_explore_data(monthly: list[dict[str, object]],
                       infra_panel: list[dict[str, object]],
                       stats: list[dict[str, object]]) -> None:
    """Static data file for docs/explore.html; the page renders it with textContent only."""
    baskets: dict[str, dict[str, object]] = {}
    fields = {
        "volume": "volume_mn", "value": "value_nominal_crore",
        "real": "value_real_mar2026_crore", "volShare": "volume_share_pct",
        "valShare": "value_share_pct", "rollVolShare": "rolling12_volume_share_pct",
        "rollValShare": "rolling12_value_share_pct", "ticket": "average_ticket_nominal_rs",
        "yoy": "volume_yoy_pct",
    }
    for row in monthly:
        basket = str(row["basket"])
        entry = baskets.setdefault(basket, {
            "label": BASKET_LABELS.get(basket, basket),
            "core": basket in CORE_BASKETS, "months": [], "rails": {}})
        if not entry["months"] or entry["months"][-1] != row["month"]:
            entry["months"].append(row["month"])
        rail = entry["rails"].setdefault(str(row["rail"]), {k: [] for k in fields})
        for key, field in fields.items():
            value = row.get(field)
            rail[key].append(round(float(value), 4) if value is not None else None)
    hhi: dict[str, dict[str, float]] = defaultdict(dict)
    trends = []
    for item in stats:
        if item["statistic"] == "hhi_volume":
            hhi[str(item["basket"])][str(item["period"])] = round(float(item["value"]), 1)
        elif item["statistic"] in ("trend_growth_volume", "annualised_growth_first12_to_last12_volume"):
            trends.append({k: (round(v, 2) if isinstance(v, float) else v)
                           for k, v in item.items()})
    payload = {
        "dataThrough": END_MONTH, "baskets": baskets, "hhi": hhi, "trends": trends,
        "infrastructure": [{k: (round(v, 3) if isinstance(v, float) else v)
                            for k, v in row.items()} for row in infra_panel],
    }
    DOCS_DATA.write_text(
        "// Generated by analysis/payment_mix_model.py from analysis/output CSVs. Do not edit.\n"
        "// Volumes: millions of transactions. Values: Rs crore. Shares: % within basket.\n"
        f"window.EXPLORE_DATA = Object.freeze({json.dumps(payload, separators=(',', ':'))});\n",
        encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: round(value, 6) if isinstance(value, float) else value
                             for field, value in row.items() if field in fields})


def label_month(month: str) -> str:
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{names[int(month[5:7]) - 1]} {month[:4]}"


def comparison_table(comparisons: list[dict[str, object]], basket: str, first: str,
                     last: str) -> list[str]:
    lines = [
        f"| Rail | Volume change | Volume-share change | Value-share change | "
        f"Real value change | Pattern |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in comparisons:
        if (item["basket"], item["first_year"], item["last_year"]) != (basket, first, last):
            continue
        real = item["real_value_growth_pct"]
        lines.append(
            f"| {item['rail']} | {float(item['volume_growth_pct']):+.1f}% | "
            f"{float(item['volume_share_change_pp']):+.2f} pp | "
            f"{float(item['value_share_change_pp']):+.2f} pp | "
            f"{f'{float(real):+.1f}%' if real is not None else 'n/a'} | {item['pattern']} |")
    return lines


def extension_sections(comparisons: list[dict[str, object]],
                       monthly: list[dict[str, object]],
                       stats: list[dict[str, object]],
                       infra_panel: list[dict[str, object]],
                       windows: dict[str, tuple[str, str]]) -> list[str]:
    first, last = "FY2022-23", "FY2025-26"
    lines = [
        "## Additional time series",
        "",
        "These baskets extend the core comparison. They reuse the same share and",
        "pattern definitions, but each basket is its own denominator, so shares are",
        "not comparable across baskets. Context baskets are descriptive background",
        "and do not change the core answer.",
        "",
        "| Basket | Window | Rails |",
        "| --- | --- | --- |",
    ]
    for basket, (start, end) in windows.items():
        rails = sorted({str(r["rail"]) for r in monthly if r["basket"] == basket})
        lines.append(f"| {BASKET_LABELS[basket]} | {start} to {end} | {', '.join(rails)} |")
    lines += [
        "",
        f"### Card channel split: {first} to {last}",
        "",
        "RBI splits each card's purchases into PoS (in-store terminal) and Others",
        "(mostly online/card-not-present). This tests whether card contraction is",
        "concentrated in the in-store channel where UPI QR competes most directly.",
        "",
    ] + comparison_table(comparisons, "merchant_channel", first, last) + [
        "",
        f"### UPI composition: P2P vs P2M, {first} to {last}",
        "",
    ] + comparison_table(comparisons, "upi_use", first, last)
    march = {(r["basket"], r["rail"]): r for r in monthly if r["month"] == END_MONTH}
    p2m = march.get(("upi_use", "UPI P2M"))
    if p2m:
        lines += ["", f"In {label_month(END_MONTH)}, P2M was "
                  f"{float(p2m['volume_share_pct']):.1f}% of UPI ecosystem transactions but "
                  f"{float(p2m['value_share_pct']):.1f}% of UPI value, with an average ticket of "
                  f"₹{float(p2m['average_ticket_nominal_rs']):,.0f}."]
    lines += [
        "",
        f"### Wider retail context: {first} to {last}",
        "",
        "Context rails (UPI total, IMPS, cards, PPI, and NEFT/AePS/NETC when the RBI",
        "workbooks are archived) are shown for scale only. NEFT includes many",
        "business and bulk payments; UPI can be funded from PPI wallets, so rails",
        "may overlap.",
        "",
    ] + comparison_table(comparisons, "context_retail", first, last)
    if "cash_context" in windows:
        lines += ["", f"### ATM cash withdrawals vs UPI P2M: {first} to {last}", ""]
        lines += comparison_table(comparisons, "cash_context", first, last)
    lines += [
        "",
        "### Trend growth (log-linear, annualised)",
        "",
        "Slope of ln(monthly volume) on time, converted to % per year. The",
        "transfer basket is also split at April 2020 (first COVID-19 lockdown) as",
        "a descriptive comparison, not an estimated structural break.",
        "",
        "| Basket | Rail | Period | Trend growth | Fit |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for item in stats:
        if (item["statistic"] == "trend_growth_volume"
                and item["basket"] in ("transfer", "merchant", "merchant_channel")
                and (item["basket"], item["rail"]) != ("merchant_channel", "UPI P2M")):
            lines.append(f"| {item['basket']} | {item['rail']} | {item['period']} | "
                         f"{float(item['value']):+.1f}% | {str(item['note']).split(', ')[-1]} |")
    lines += [
        "",
        "### Seasonality",
        "",
        "Seasonal index of monthly volume (mean = 100), from the ratio to a centred",
        "2x12 moving average. Shown for the peak and trough calendar months only;",
        "the full index is in `timeseries_stats.csv`.",
        "",
        "| Basket | Rail | Peak month | Peak index | Trough month | Trough index |",
        "| --- | --- | --- | ---: | --- | ---: |",
    ]
    seasonal: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
    for item in stats:
        if item["statistic"] == "seasonal_index_volume":
            seasonal[(str(item["basket"]), str(item["rail"]))].append(
                (float(item["value"]), str(item["period"])[-2:]))
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for (basket, rail), values in sorted(seasonal.items()):
        if (basket not in ("transfer", "merchant", "merchant_channel")
                or (basket, rail) == ("merchant_channel", "UPI P2M")):
            continue
        hi, lo = max(values), min(values)
        lines.append(f"| {basket} | {rail} | {names[int(hi[1]) - 1]} | {hi[0]:.1f} | "
                     f"{names[int(lo[1]) - 1]} | {lo[0]:.1f} |")
    lines += [
        "",
        "### Concentration within each basket (volume HHI, 0–10,000)",
        "",
        "| Basket | First month | HHI | Last month | HHI |",
        "| --- | --- | ---: | --- | ---: |",
    ]
    hhi: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for item in stats:
        if item["statistic"] == "hhi_volume":
            hhi[str(item["basket"])].append((str(item["period"]), float(item["value"])))
    for basket, values in hhi.items():
        values.sort()
        lines.append(f"| {basket} | {values[0][0]} | {values[0][1]:,.0f} | "
                     f"{values[-1][0]} | {values[-1][1]:,.0f} |")
    lines += ["", "### Acceptance infrastructure and usage intensity", ""]
    if infra_panel:
        a, b = infra_panel[0], infra_panel[-1]
        lines += [
            "| Measure | " + str(a["month"]) + " | " + str(b["month"]) + " |",
            "| --- | ---: | ---: |",
        ]
        for field in INFRA_FIELDS[1:]:
            if a[field] is not None and b[field] is not None:
                lines.append(f"| {field} | {float(a[field]):,.2f} | {float(b[field]):,.2f} |")
        lines += ["", "Rising acceptance points alongside falling use per terminal is",
                  "consistent with, but does not test, cross-side network effects."]
    else:
        lines += ["Pending. Import RBI PSI Part III counts (cards outstanding, PoS",
                  "terminals, Bharat QR, UPI QR codes, ATMs) from official monthly",
                  "release pages with `scripts/import_rbi_psi_pages.py` to fill this section."]
    return lines + [""]


def write_findings(annual: list[dict[str, object]],
                   comparisons: list[dict[str, object]],
                   monthly: list[dict[str, object]], diagnostics: dict[str, float],
                   cross_source_discrepancies: list[dict[str, object]],
                   stats: list[dict[str, object]],
                   infra_panel: list[dict[str, object]],
                   windows: dict[str, tuple[str, str]]) -> None:
    annual_index = {(row["basket"], row["fiscal_year"], row["rail"]): row
                    for row in annual}
    march = {(row["basket"], row["rail"], row["month"]): row
             for row in monthly if row["month"] == "2026-03"}
    yoy_months = sorted({str(row["month"]) for row in monthly if row["basket"] == "merchant"
                         and row["volume_pattern_yoy"] is not None})
    lines = [
        "# What the payment-mix model says",
        "",
        "Data freeze: March 2026. Figures below are computed from the annual CSV extracts.",
        "Volumes are transactions, values are rupees, and shares are within the named",
        "analytical comparison basket. These shares are not economy-wide market shares.",
        "",
        "## Merchant payments: FY2022-23 to FY2025-26",
        "",
        "| Rail | FY22-23 volume (bn) | FY25-26 volume (bn) | Volume change | Share change | Pattern |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in comparisons:
        if item["basket"] != "merchant" or item["first_year"] != "FY2022-23":
            continue
        before = annual_index[("merchant", item["first_year"], item["rail"])]
        after = annual_index[("merchant", item["last_year"], item["rail"])]
        lines.append(
            f"| {item['rail']} | {float(before['volume_mn']) / 1000:,.2f} | "
            f"{float(after['volume_mn']) / 1000:,.2f} | "
            f"{float(item['volume_growth_pct']):+.1f}% | "
            f"{float(item['volume_share_change_pp']):+.2f} pp | {item['pattern']} |"
        )
    lines += [
        "",
        "The fixed merchant basket includes UPI P2M, domestic credit-card purchases,",
        "and domestic debit-card purchases. RBI volume (lakh) is divided by 10 to match",
        "NPCI volume (millions). A falling share alongside rising transactions is",
        "relative share loss; falling transactions and share is absolute contraction.",
        "",
        "### Value and ticket size over the same fiscal years",
        "",
        "| Rail | Real value change | Nominal value-share change | Real ticket change |",
        "| --- | ---: | ---: | ---: |",
    ]
    for item in comparisons:
        if item["basket"] != "merchant" or item["first_year"] != "FY2022-23":
            continue
        lines.append(
            f"| {item['rail']} | {float(item['real_value_growth_pct']):+.1f}% | "
            f"{float(item['value_share_change_pp']):+.2f} pp | "
            f"{float(item['real_ticket_growth_pct']):+.1f}% |"
        )
    lines += [
        "",
        "Real value and ticket use MoSPI CPI linked to the 2024 base and are",
        "expressed in March 2026 rupees. Value shares use nominal values within",
        "the same month or fiscal year.",
        "",
        "### March 2026 transaction size",
        "",
        "| Rail | Transactions (bn) | Nominal value (₹ lakh crore) | Average ticket (₹) | Volume share | Value share |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rail in ("UPI P2M", "credit card", "debit card"):
        row = march[("merchant", rail, "2026-03")]
        lines.append(
            f"| {rail} | {float(row['volume_mn']) / 1000:,.3f} | "
            f"{float(row['value_nominal_crore']) / 100000:,.2f} | "
            f"{float(row['average_ticket_nominal_rs']):,.0f} | "
            f"{float(row['volume_share_pct']):.2f}% | "
            f"{float(row['value_share_pct']):.2f}% |"
        )
    lines += [
        "",
        "Average ticket = value in ₹ crore × 10 / volume in millions. Lower UPI P2M",
        "ticket size is consistent with different transaction mixes; it does not show",
        "that a given card transaction switched to UPI.",
        "",
        f"### Year-on-year pattern counts ({len(yoy_months)} matched months, "
        f"{label_month(yoy_months[0])}–{label_month(yoy_months[-1])})",
        "",
        "| Rail | Absolute contraction | Relative share loss | Co-expansion | Other |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for rail in ("credit card", "debit card"):
        patterns = Counter(str(row["volume_pattern_yoy"]) for row in monthly
                           if row["basket"] == "merchant" and row["rail"] == rail
                           and row["volume_pattern_yoy"] is not None)
        other = sum(n for pattern, n in patterns.items() if pattern not in (
            "absolute contraction", "relative share loss", "co-expansion"))
        lines.append(f"| {rail} | {patterns['absolute contraction']} | "
                     f"{patterns['relative share loss']} | "
                     f"{patterns['co-expansion']} | {other} |")
    lines += [
        "",
        "## UPI and IMPS: FY2016-17 to FY2025-26",
        "",
        "| Rail | FY16-17 volume (bn) | FY25-26 volume (bn) | Volume change | Share change | Pattern |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in comparisons:
        if item["basket"] != "transfer" or item["first_year"] != "FY2016-17":
            continue
        before = annual_index[("transfer", item["first_year"], item["rail"])]
        after = annual_index[("transfer", item["last_year"], item["rail"])]
        lines.append(
            f"| {item['rail']} | {float(before['volume_mn']) / 1000:,.2f} | "
            f"{float(after['volume_mn']) / 1000:,.2f} | "
            f"{float(item['volume_growth_pct']):+.1f}% | "
            f"{float(item['volume_share_change_pp']):+.2f} pp | {item['pattern']} |"
        )
    lines += [
        "",
        "The UPI–IMPS basket is a separate comparison. It should not be added to the",
        "merchant basket because total UPI includes P2M, P2P and other use cases.",
        "UPI's very large percentage growth starts from a near-zero launch base;",
        "the change in transaction counts and basket shares is more informative.",
        "",
        "### Most recent full year: FY2024-25 to FY2025-26",
        "",
        "| Basket | Rail | Volume change | Share change | Real value change | Pattern |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for item in comparisons:
        if item["first_year"] != "FY2024-25" or item["basket"] not in CORE_BASKETS:
            continue
        real = item["real_value_growth_pct"]
        real_text = f"{float(real):+.1f}%" if real is not None else "n/a"
        lines.append(
            f"| {item['basket']} | {item['rail']} | "
            f"{float(item['volume_growth_pct']):+.1f}% | "
            f"{float(item['volume_share_change_pp']):+.2f} pp | "
            f"{real_text} | {item['pattern']} |"
        )
    lines += [""] + extension_sections(comparisons, monthly, stats, infra_panel, windows)
    lines += [
        "## Inflation and source checks",
        "",
        "MoSPI's linked all-India CPI expresses 2022–26 values and tickets in March",
        "2026 rupees. Nominal shares are unchanged by a common monthly deflator,",
        "although annual real-value shares can differ slightly because monthly rail",
        "composition differs. The panel CSV contains nominal and real measures.",
        "",
        f"Source coverage: {len([r for r in monthly if r['basket'] == 'transfer']) // 2} "
        "transfer months and "
        f"{len([r for r in monthly if r['basket'] == 'merchant']) // 3} merchant months. "
        "No missing months were filled.",
        "",
        "Maximum absolute source differences (published rounding allowed for",
        "within-source identities):",
        "",
        "| Check | Maximum difference |",
        "| --- | ---: |",
    ]
    for name, difference in diagnostics.items():
        lines.append(f"| {name} | {difference:.6g} |")
    if cross_source_discrepancies:
        lines += [
            "",
            "NPCI product and ecosystem UPI totals are distinct source tables and",
            "must not be silently substituted for each other. Months with a",
            "difference above 0.11 in either published unit:",
            "",
            "| Month | Ecosystem minus product volume (mn) | Ecosystem minus product value (₹ crore) |",
            "| --- | ---: | ---: |",
        ]
        for item in cross_source_discrepancies:
            lines.append(
                f"| {item['month']} | "
                f"{float(item['ecosystem_minus_product_volume_mn']):+,.2f} | "
                f"{float(item['ecosystem_minus_product_value_crore']):+,.2f} |"
            )
    lines += [
        "",
        "## Interpretation limits",
        "",
        "These are descriptive associations in national monthly totals. They cannot",
        "identify individual switching, substitution elasticities, merchant acceptance,",
        "or causal network effects. UPI-funded card transactions may overlap the card",
        "series. IMPS and UPI have different use cases; P2M and card totals are not",
        "identical products. RBI own-month release observations are provisional, and the",
        "original RBI workbooks are "
        + ("archived for the months listed in the source manifest." if infra_panel
           else "not yet archived locally.")
        + " PPI is excluded from the",
        "primary baskets because it mixes uses; it appears only in the context basket.",
        "The card 'Others' channel is mostly, but not only, online purchases. Trend",
        "slopes, seasonal indices and HHI summarise national totals and are not",
        "estimates of demand or substitution. Acceptance-infrastructure counts measure",
        "terminals and QR codes deployed, not active merchants. The P2P/P2M monthly",
        "series was transcribed from NPCI's official selector table; six original",
        "month workbooks are archived locally. The announced October 2026 merchant",
        "charge is outside this data window.",
        "",
    ]
    (OUTPUT / "findings.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    upi = read_series("npci_upi_product_statistics.csv", TRANSFER_START)
    imps = read_series("npci_imps_product_statistics.csv", TRANSFER_START)
    split = read_series("npci_upi_p2p_p2m_transactions.csv", None)
    rbi = read_series("rbi_psi_card_ppi_own_month.csv", None)
    cpi = read_series("mospi_cpi_combined_monthly.csv", None)
    other = read_series("rbi_psi_other_rails.csv", None, optional=True)
    infra = read_series("rbi_psi_infrastructure.csv", None, optional=True)
    # Longest window in which P2M, cards and CPI are all observed.
    merchant_start = max(min(split), min(rbi), min(cpi))
    diagnostics, cross_source_discrepancies = reconcile(
        upi, split, rbi, cpi, merchant_start, infra)
    monthly = build_monthly(upi, imps, split, rbi, cpi, merchant_start, other)
    infra_panel = build_infrastructure(infra, split, rbi)
    stats = build_timeseries_stats(monthly)
    annual = annualize(monthly)
    comparisons = (
        compare_years(annual, "transfer", "FY2016-17", "FY2025-26")
        + compare_years(annual, "merchant", "FY2022-23", "FY2025-26")
        + compare_years(annual, "transfer", "FY2024-25", "FY2025-26")
        + compare_years(annual, "merchant", "FY2024-25", "FY2025-26")
    )
    windows: dict[str, tuple[str, str]] = {}
    for row in monthly:
        basket = str(row["basket"])
        if basket in CORE_BASKETS:
            continue
        start = windows.get(basket, (str(row["month"]), ""))[0]
        windows[basket] = (min(start, str(row["month"])), str(row["month"]))
    for basket in windows:
        for first in ("FY2022-23", "FY2024-25"):
            comparisons += compare_years(annual, basket, first, "FY2025-26")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT / "monthly_panel.csv", monthly, [
        "month", "basket", "rail", "volume_mn", "value_nominal_crore",
        "value_real_mar2026_crore", "average_ticket_nominal_rs",
        "average_ticket_real_mar2026_rs", "basket_volume_mn",
        "volume_share_pct", "value_share_pct", "volume_yoy_pct",
        "volume_share_yoy_pp", "real_value_yoy_pct", "volume_pattern_yoy",
        "source_url", "rolling12_volume_mn", "rolling12_value_nominal_crore",
        "rolling12_volume_share_pct", "rolling12_value_share_pct",
    ])
    write_csv(OUTPUT / "infrastructure_panel.csv", infra_panel, INFRA_FIELDS)
    write_csv(OUTPUT / "timeseries_stats.csv", stats, [
        "basket", "rail", "statistic", "period", "value", "unit", "note",
    ])
    figures = write_figures(monthly, infra_panel, stats)
    write_explore_data(monthly, infra_panel, stats)
    write_csv(OUTPUT / "annual_summary.csv", annual, [
        "basket", "fiscal_year", "rail", "months", "volume_mn",
        "value_nominal_crore", "value_real_mar2026_crore",
        "average_ticket_nominal_rs", "average_ticket_real_mar2026_rs",
        "volume_share_pct", "value_share_pct",
    ])
    write_csv(OUTPUT / "period_comparison.csv", comparisons, [
        "basket", "rail", "first_year", "last_year", "volume_growth_pct",
        "volume_share_change_pp", "value_share_change_pp", "nominal_value_growth_pct",
        "real_value_growth_pct", "real_ticket_growth_pct", "pattern",
    ])
    with (OUTPUT / "source_checks.json").open("w", encoding="utf-8") as stream:
        json.dump({"coverage": {
                       "transfer_months": len(month_range(TRANSFER_START, END_MONTH)),
                       "merchant_months": len(month_range(merchant_start, END_MONTH))},
                   "extended_windows": {basket: {"start": start, "end": end}
                                        for basket, (start, end) in windows.items()},
                   "optional_series": {
                       "rbi_psi_other_rails": len(other),
                       "rbi_psi_infrastructure": len(infra)},
                   "maximum_absolute_source_difference": diagnostics,
                   "cross_source_discrepancies": cross_source_discrepancies},
                  stream, indent=2)
        stream.write("\n")
    write_findings(annual, comparisons, monthly, diagnostics,
                   cross_source_discrepancies, stats, infra_panel, windows)
    print(f"Wrote payment-mix analysis to {OUTPUT} ({len(figures)} figures) "
          f"and {DOCS_DATA.relative_to(REPO)}")


if __name__ == "__main__":
    main()
