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
END_MONTH = "2026-03"
TRANSFER_START = "2016-04"
MERCHANT_START = "2022-01"
BASE_MONTH = "2026-03"


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


def read_series(filename: str, start: str) -> dict[str, dict[str, str]]:
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
    expected = set(month_range(start, END_MONTH))
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
) -> tuple[dict[str, float], list[dict[str, object]]]:
    maxima: dict[str, float] = defaultdict(float)
    cross_source_discrepancies: list[dict[str, object]] = []
    for month in month_range(MERCHANT_START, END_MONTH):
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


def build_monthly(
    upi: dict[str, dict[str, str]], imps: dict[str, dict[str, str]],
    split: dict[str, dict[str, str]], rbi: dict[str, dict[str, str]],
    cpi: dict[str, dict[str, str]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    cpi_base = numeric(cpi[BASE_MONTH], "cpi_combined_index_linked_2024")
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
        if month < MERCHANT_START:
            continue
        sources = (
            ("UPI P2M", numeric(split[month], "p2m_volume_mn"),
             numeric(split[month], "p2m_value_crore"), split[month]["source_url"]),
            ("credit card", numeric(rbi[month], "credit_total_volume_lakh") / 10,
             numeric(rbi[month], "credit_total_value_crore"),
             rbi[month]["rbi_release_page_url"]),
            ("debit card", numeric(rbi[month], "debit_total_volume_lakh") / 10,
             numeric(rbi[month], "debit_total_value_crore"),
             rbi[month]["rbi_release_page_url"]),
        )
        for rail, volume, value, url in sources:
            rows.append(rail_row(month, "merchant", rail, volume, value,
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
    return rows


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


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: round(value, 6) if isinstance(value, float) else value
                             for field, value in row.items() if field in fields})


def write_findings(annual: list[dict[str, object]],
                   comparisons: list[dict[str, object]],
                   monthly: list[dict[str, object]], diagnostics: dict[str, float],
                   cross_source_discrepancies: list[dict[str, object]]) -> None:
    annual_index = {(row["basket"], row["fiscal_year"], row["rail"]): row
                    for row in annual}
    march = {(row["basket"], row["rail"], row["month"]): row
             for row in monthly if row["month"] == "2026-03"}
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
        "### Year-on-year pattern counts (39 matched months, Jan 2023–Mar 2026)",
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
        if item["first_year"] != "FY2024-25":
            continue
        real = item["real_value_growth_pct"]
        real_text = f"{float(real):+.1f}%" if real is not None else "n/a"
        lines.append(
            f"| {item['basket']} | {item['rail']} | "
            f"{float(item['volume_growth_pct']):+.1f}% | "
            f"{float(item['volume_share_change_pp']):+.2f} pp | "
            f"{real_text} | {item['pattern']} |"
        )
    lines += [
        "",
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
        "original RBI workbooks are not yet archived locally. PPI is excluded from the",
        "primary baskets because it mixes uses. The P2P/P2M monthly series was",
        "transcribed from NPCI's official selector table; six original month workbooks",
        "are archived locally. The announced October 2026 merchant",
        "charge is outside this data window.",
        "",
    ]
    (OUTPUT / "findings.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    upi = read_series("npci_upi_product_statistics.csv", TRANSFER_START)
    imps = read_series("npci_imps_product_statistics.csv", TRANSFER_START)
    split = read_series("npci_upi_p2p_p2m_transactions.csv", MERCHANT_START)
    rbi = read_series("rbi_psi_card_ppi_own_month.csv", MERCHANT_START)
    cpi = read_series("mospi_cpi_combined_monthly.csv", MERCHANT_START)
    diagnostics, cross_source_discrepancies = reconcile(upi, split, rbi, cpi)
    monthly = build_monthly(upi, imps, split, rbi, cpi)
    annual = annualize(monthly)
    comparisons = (
        compare_years(annual, "transfer", "FY2016-17", "FY2025-26")
        + compare_years(annual, "merchant", "FY2022-23", "FY2025-26")
        + compare_years(annual, "transfer", "FY2024-25", "FY2025-26")
        + compare_years(annual, "merchant", "FY2024-25", "FY2025-26")
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT / "monthly_panel.csv", monthly, [
        "month", "basket", "rail", "volume_mn", "value_nominal_crore",
        "value_real_mar2026_crore", "average_ticket_nominal_rs",
        "average_ticket_real_mar2026_rs", "basket_volume_mn",
        "volume_share_pct", "value_share_pct", "volume_yoy_pct",
        "volume_share_yoy_pp", "real_value_yoy_pct", "volume_pattern_yoy",
        "source_url",
    ])
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
                       "merchant_months": len(month_range(MERCHANT_START, END_MONTH))},
                   "maximum_absolute_source_difference": diagnostics,
                   "cross_source_discrepancies": cross_source_discrepancies},
                  stream, indent=2)
        stream.write("\n")
    write_findings(annual, comparisons, monthly, diagnostics,
                   cross_source_discrepancies)
    print(f"Wrote payment-mix analysis to {OUTPUT}")


if __name__ == "__main__":
    main()
