#!/usr/bin/env python3
"""Spliced long card series, structural-break tests and the expansion-phase hypotheses.

This is the secondary analysis. The primary sample is the mature-UPI phase (June 2021 onward,
consistently classified RBI card data) tested in hypothesis_tests.py, which also publishes
these results. Run alone to write only the CSV outputs: python3 analysis/long_series.py

Bridging assumptions for the card splice:
  1. From June 2021 card payments are RBI PSI 4.1/4.2 (PoS + Others), the consistently
     classified series.
  2. Before June 2021 they are the all-bank "Total" row of RBI's bank-wise ATM/PoS/card pages.
     In the pre-March-2022 layouts that page reports card use only "at ATM" and "at PoS"; the
     PoS column covers every card payment, online included (it equals PSI PoS + Others in the
     overlap), so it maps to PSI totals, not to PSI "PoS based".
  3. The bank-wise series is scaled by k, the geometric-mean ratio PSI / bank-wise over the nine
     overlap months June 2021 - February 2022 (separately for debit/credit, volume/value).
  4. k is checked out of sample against the PSI values for June 2020 - May 2021 printed in the
     following year's releases (their "same month last year" column).
  5. RBI's June 2021 reclassification is assumed to change presentation (PoS vs Others split,
     which banks report), not the total of card payments; a level dummy at June 2021 tests this.
"""

from __future__ import annotations

import csv
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from payment_mix_model import END_MONTH, INPUT, OUTPUT, month_range, shift_month  # noqa: E402
from hypothesis_tests import (  # noqa: E402
    D12, HAC_LAGS, Z95, chart, combine, derivation, diff, drift, drift_formulas, drift_hypothesis,
    drift_line, drift_step, growth_chart, hac_cov, hypothesis, label_month, ln, logodds, ols, pct,
    p_two_sided, sub, verdict, yoy_example)

LONG_START = "2014-06"
UPI_START = "2016-04"
SPLICE_START = "2021-06"
OVERLAP = ("2021-06", "2022-02")
# Months whose year-on-year change is averaged: levels Apr 2017 - Feb 2020. Earlier comparisons
# would set post-demonetisation months against pre-demonetisation bases (card use roughly doubled).
EXPANSION = ("2018-04", "2020-02")
DEMONETISATION = ("2016-11", "2017-03")
COVID = ("2020-03", "2020-12")
COVID_WAVE2 = ("2021-04", "2021-06")
SAME_ACCOUNT = "2018-08"
ZERO_MDR = "2020-01"
SPLIT_START = "2020-04"
GROUP = "Expansion phase 2017–20 (spliced, secondary)"

BREAKS = [
    {"id": "DM", "month": "2016-11", "name": "Demonetisation",
     "what": "Withdrawal of ₹500/₹1,000 notes; card and wallet use spiked Nov 2016 – Mar 2017.",
     "affects": "Card payments and UPI levels in Nov 2016 – Mar 2017 (not in the required list; added because the long series crosses it)."},
    {"id": "SA", "month": SAME_ACCOUNT, "name": "NPCI same-account exclusion",
     "what": "From Aug 2018 NPCI excludes UPI transactions debiting and crediting the same account.",
     "affects": "Total UPI volume and value (transfer basket) — a one-off downward level shift."},
    {"id": "MDR", "month": ZERO_MDR, "name": "Zero MDR on UPI and RuPay debit",
     "what": "Merchant discount rate set to zero for UPI and RuPay debit cards from 1 Jan 2020 (PSS Act s.10A).",
     "affects": "Merchant cost of accepting UPI and RuPay debit relative to credit cards (and Visa/Mastercard debit)."},
    {"id": "CV", "month": COVID[0], "name": "COVID-19",
     "what": "National lockdown from 25 Mar 2020, phased reopening to Dec 2020; second wave Apr – Jun 2021.",
     "affects": "Every rail; debit-card payments in Apr 2020 were about half their Feb 2020 level."},
    {"id": "SP", "month": SPLIT_START, "name": "Start of NPCI's P2P/P2M split",
     "what": "NPCI's ecosystem statistics split UPI into P2P and P2M from Apr 2020 (API answers 404 earlier).",
     "affects": "Sample boundary for UPI P2M: merchant-basket comparisons with P2M start Apr 2020."},
    {"id": "RC", "month": SPLICE_START, "name": "RBI card reclassification (splice point)",
     "what": "RBI PSI's consistently classified card series (PoS + Others) starts Jun 2021.",
     "affects": "Card series source changes from bank-wise totals to PSI; a level dummy tests the bridge."},
]
MARKERS = [(b["month"], b["id"]) for b in BREAKS]


def read_all(filename: str) -> dict[str, dict[str, str]]:
    """Every observation of one by_year file, without the coverage check of read_series."""
    rows = {}
    for path in sorted(INPUT.glob(f"*/{filename}")):
        with path.open(newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                rows[row["month"][:7]] = row
    return rows


def inside(month: str, window: tuple[str, str]) -> bool:
    return window[0] <= month <= window[1]


def geo_mean(values: list[float]) -> float:
    return math.exp(sum(math.log(v) for v in values) / len(values))


# ---------- Data and splice ----------

def load() -> dict[str, object]:
    months = month_range(LONG_START, END_MONTH)
    bank = read_all("rbi_bankwise_card_totals.csv")
    psi = read_all("rbi_psi_card_ppi_own_month.csv")
    prior = read_all("rbi_psi_card_prior_year_column.csv")
    upi = read_all("npci_upi_product_statistics.csv")
    imps = read_all("npci_imps_product_statistics.csv")
    split = read_all("npci_upi_p2p_p2m_transactions.csv")
    s: dict[str, object] = {"months": months}
    block = __doc__.split("Bridging assumptions for the card splice:")[1]
    splice = {"assumptions": [" ".join(p.split()) for p in re.split(r"\n\s*\d\.\s", block) if p.strip()],
              "overlap": list(OVERLAP), "factors": []}
    for rail in ("debit", "credit"):
        for measure, bank_field, psi_field, scale_bank, scale_psi, unit in (
                ("n", f"{rail}_payments_n", f"{rail}_total_volume_lakh", 1e-6, 0.1, "million"),
                ("v", f"{rail}_payments_v_crore", f"{rail}_total_value_crore", 1.0, 1.0, "₹ crore")):
            raw = {m: float(r[bank_field]) * scale_bank for m, r in bank.items()}
            official = {m: float(r[psi_field]) * scale_psi for m, r in psi.items()}
            overlap = [m for m in month_range(*OVERLAP)]
            ratios = [official[m] / raw[m] for m in overlap]
            k = geo_mean(ratios)
            check = [(m, float(prior[m][psi_field]) * scale_psi, k * raw[m]) for m in sorted(prior)]
            errors = [abs(b / a - 1) for _, a, b in check]
            s[f"{rail}_{measure}"] = [official[m] if m >= SPLICE_START else (k * raw[m] if m in raw else None)
                                      for m in months]
            s[f"{rail}_{measure}_bank"] = [raw.get(m) for m in months]
            s[f"{rail}_{measure}_psi"] = [official.get(m) for m in months]
            splice["factors"].append({
                "series": f"{rail.capitalize()} card {'volume' if measure == 'n' else 'value'}", "unit": unit,
                "k": round(k, 4), "overlapMin": round(min(ratios), 4), "overlapMax": round(max(ratios), 4),
                "checkMonths": f"{label_month(check[0][0])} – {label_month(check[-1][0])}",
                "checkMape": round(100 * sum(errors) / len(errors), 2),
                "checkMax": round(100 * max(errors), 2)})
    s["upi_n"] = [float(upi[m]["volume_mn"]) if m in upi else None for m in months]
    s["imps_n"] = [float(imps[m]["volume_mn"]) if m in imps else None for m in months]
    s["p2m_n"] = [float(split[m]["p2m_volume_mn"]) if m in split else None for m in months]
    s["upi_share_transfer"] = [u / (u + i) if u and i else None for u, i in zip(s["upi_n"], s["imps_n"])]
    s["upi_share_cards"] = [u / (u + d + c) if u and d else None
                            for u, d, c in zip(s["upi_n"], s["debit_n"], s["credit_n"])]
    s["p2m_share_cards"] = [p / (p + d + c) if p and d else None
                            for p, d, c in zip(s["p2m_n"], s["debit_n"], s["credit_n"])]
    gaps = {m: abs(float(split[m]["total_volume_mn"]) - float(upi[m]["volume_mn"]))
            for m in sorted(split) if m in upi}
    splice["splitConsistency"] = {
        "first": min(split), "months": len(gaps),
        "matching": sum(g <= 0.11 for g in gaps.values()),
        "exceptions": [m for m, g in gaps.items() if g > 0.11]}
    # The bank-wise pages switch from Rs million to Rs lakh in Jul 2019; the ticket must stay continuous.
    i = months.index("2019-07")
    tickets = [s["credit_v_bank"][j] * 1e7 / (s["credit_n_bank"][j] * 1e6) for j in (i - 1, i)]
    splice["unitCheck"] = (f"Credit-card average ticket ₹{tickets[0]:,.0f} (Jun 2019, published in ₹ million) "
                           f"vs ₹{tickets[1]:,.0f} (Jul 2019, ₹ lakh)")
    s["splice"] = splice
    return s


# ---------- Known-date break model ----------

def chi2_sf(x: float, df: int) -> float:
    """Upper tail of the chi-square distribution (regularised gamma series)."""
    if x <= 0:
        return 1.0
    a, z = df / 2, x / 2
    term = math.exp(a * math.log(z) - z - math.lgamma(a + 1))
    total, n = term, 0
    while term > 1e-15 * total and n < 5000:
        n += 1
        term *= z / (a + n)
        total += term
    return max(0.0, 1.0 - total)


def wald(beta: list[float], cov: list[list[float]], idx: list[int]) -> float:
    from hypothesis_tests import invert
    b = [beta[i] for i in idx]
    v = invert([[cov[i][j] for j in idx] for i in idx])
    return sum(b[i] * v[i][j] * b[j] for i in range(len(b)) for j in range(len(b)))


def break_fit(months, y, tau, window, quadratic=False, splice_dummy=False, seasonal=True, slope=True):
    """ln y = trend + month dummies + δ·1[t≥τ] + θ·(t−τ)·1[t≥τ] + one dummy per COVID/demonetisation month.

    slope=False drops θ (level-shift test, χ²(1))."""
    rows = [(m, v) for m, v in zip(months, y) if inside(m, window) and v is not None]
    ti = {m: i for i, m in enumerate(month_range(window[0], window[1]))}
    t0 = ti[tau]
    event_months = [m for m, _ in rows if inside(m, COVID) or inside(m, COVID_WAVE2) or inside(m, DEMONETISATION)]
    calendar = range(2, 13) if seasonal else ()
    x, names = [], ["const", "trend"] + (["trend2"] if quadratic else []) + [f"m{c}" for c in calendar]
    names += ["level"] + (["slope"] if slope else []) + (["splice"] if splice_dummy else [])
    names += [f"ev_{m}" for m in event_months]
    for m, _ in rows:
        t = (ti[m] - t0) / 12
        after = 1.0 if m >= tau else 0.0
        row = [1.0, t] + ([t * t] if quadratic else []) + [1.0 if int(m[5:7]) == c else 0.0 for c in calendar]
        row += [after] + ([t * after] if slope else []) + ([1.0 if m >= SPLICE_START else 0.0] if splice_dummy else [])
        row += [1.0 if m == e else 0.0 for e in event_months]
        x.append(row)
    fit = ols([math.log(v) if not isinstance(v, tuple) else v[0] for _, v in rows], x)
    cov = hac_cov(fit, x, HAC_LAGS)
    beta = fit["beta"]
    se = [math.sqrt(max(cov[i][i], 0.0)) for i in range(len(beta))]
    tested = [names.index("level")] + ([names.index("slope")] if slope else [])
    w = wald(beta, cov, tested)
    li = tested[0]
    out = {"n": len(rows), "k": len(beta), "window": f"{label_month(window[0])} – {label_month(window[1])}",
           "level": beta[li], "levelSe": se[li], "slope": None, "slopeSe": None,
           "trend": beta[1], "trendSe": se[1], "wald": w, "df": len(tested), "p": chi2_sf(w, len(tested))}
    if slope:
        out["slope"], out["slopeSe"] = beta[tested[1]], se[tested[1]]
    # Each COVID dummy fits one month exactly, so its HAC variance is degenerate: report the
    # deviations from trend, not a joint test.
    covid = [(m, beta[names.index(f"ev_{m}")]) for m in event_months if inside(m, COVID)]
    if covid:
        out["covidMean"] = sum(b for _, b in covid) / len(covid)
        out["covidTroughMonth"], out["covidTrough"] = min(covid, key=lambda c: c[1])
        out["covidMonths"] = len(covid)
    if splice_dummy:
        i = names.index("splice")
        out["splice"], out["spliceSe"] = beta[i], se[i]
    return out


def break_tests(s) -> list[dict[str, object]]:
    months = s["months"]
    ratio = [d / c if d and c else None for d, c in zip(s["debit_n"], s["credit_n"])]
    logit_share = [(math.log(v / (1 - v)),) if v else None for v in s["upi_share_transfer"]]
    # Aug 2018: ±12 months, quadratic adoption trend, no month dummies, level shift only (24 observations
    # cannot separate a slope break from UPI's adoption curve; the exclusion is a one-off level effect).
    # Jan 2020: Apr 2017 - Mar 2023, month dummies, COVID month dummies, Jun 2021 splice dummy.
    local = ("2017-08", "2019-07")
    around_mdr = ("2017-04", "2023-03")
    specs = [
        ("SA", "UPI total volume", s["upi_n"], local, True, False, False),
        ("SA", "UPI share of transfers (log-odds)", logit_share, local, True, False, False),
        ("SA", "IMPS volume (placebo: not affected)", s["imps_n"], local, True, False, False),
        ("MDR", "Debit-card payments (spliced)", s["debit_n"], around_mdr, False, True, True),
        ("MDR", "Credit-card payments (spliced; no zero MDR)", s["credit_n"], around_mdr, False, True, True),
        ("MDR", "Debit ÷ credit payments (nets common shocks)", ratio, around_mdr, False, True, True),
    ]
    tau = {"SA": SAME_ACCOUNT, "MDR": ZERO_MDR}
    out = []
    for bid, label, y, window, quad, splice, seasonal in specs:
        f = break_fit(months, y, tau[bid], window, quad, splice, seasonal, slope=bid != "SA")
        out.append({"break": bid, "series": label, **f,
                    "levelPct": 100 * (math.exp(f["level"]) - 1),
                    "slopePp": None if f["slope"] is None else 100 * f["slope"],
                    "trendPp": 100 * f["trend"], "quadratic": quad, "seasonal": seasonal})
    return out


# ---------- Expansion-phase hypotheses ----------

def expansion_growth(s, key, upi=False, window=EXPANSION):
    """Year-on-year log changes for window months; comparisons that straddle a break are None."""
    out = []
    for m, g in zip(s["months"], diff(ln(s[key]))):
        base = shift_month(m, -12)
        straddles = (inside(m, DEMONETISATION) or inside(base, DEMONETISATION)
                     or (upi and base < SAME_ACCOUNT <= m))
        out.append(g if inside(m, window) and not straddles else None)
    return out


def expansion_text(series, upi=False):
    kept = [v for v in series if v is not None]
    return (f"Mean 12-month log change over UPI's expansion phase: {len(kept)} year-on-year comparisons, "
            f"{label_month(EXPANSION[0])} (vs {label_month(shift_month(EXPANSION[0], -12))}) to "
            f"{label_month(EXPANSION[1])}, i.e. levels from Apr 2017 to the last pre-COVID month; starting "
            "in Apr 2018 keeps demonetisation (Nov 2016 – Mar 2017) out of every base month"
            + ("; comparisons straddling the Aug 2018 same-account exclusion are left out" if upi else "")
            + "; Newey-West (12-lag) standard errors")


CARD_DATA = ("RBI bank-wise ATM/PoS/card statistics (all-bank totals) scaled to RBI PSI, spliced at Jun 2021 "
             "(see the splice table)")


def e1(s):
    g = expansion_growth(s, "debit_n")
    result = drift_hypothesis(
        "E1", GROUP, "Debit already contracted while UPI expanded",
        "Re-test of H1 on the expansion phase: debit-card payment volume fell year on year in Apr 2017 – Feb 2020.",
        "Same closeness-of-substitutes argument as H1. If debit grew instead, absolute contraction is a "
        "mature-phase outcome that needed UPI's merchant network to reach scale first.",
        "Mean of Δ12 ln(debit-card payments)", "<0", g, "growth",
        chart("lines", "Card payments per month, spliced long series", s["months"],
              [("Debit card", s["debit_n"]), ("Credit card", s["credit_n"])], "million transactions",
              markers=MARKERS),
        CARD_DATA, D12 + r"\ln N^{D}_t", r"N^{D}_t = \text{debit-card payments (spliced series)}",
        question="Was debit-card payment volume lower than a year earlier during the expansion phase?",
        test_text=expansion_text(g), months=s["months"])
    return result


def e2(s):
    gc = expansion_growth(s, "credit_n")
    # Kept: the Aug 2018 exclusion can only lower measured UPI growth, i.e. it works against E2.
    gap = sub(expansion_growth(s, "upi_n"), gc)
    strict = drift(sub(expansion_growth(s, "upi_n", upi=True), gc))
    dc, dg = drift(gc), drift(gap)
    parts = [verdict(dc["lo"], dc["hi"], ">0"), verdict(dg["lo"], dg["hi"], ">0")]
    return hypothesis(
        note=(f"Leaving out the {dg['n'] - strict['n']} UPI comparisons that straddle the Aug 2018 same-account "
              f"exclusion: UPI minus credit {drift_line(strict)[0]} ({strict['n']} comparisons)."),
        id="E2", group=GROUP, title="Credit grew but lost share during the expansion",
        statement="Credit-card payments grew in Apr 2017 – Feb 2020, but more slowly than total UPI.",
        framework="Credit is an imperfect substitute for UPI (H2). Total UPI is the comparator because NPCI's "
                  "P2M split starts only in Apr 2020; it overstates UPI's merchant use, so the gap is an upper bound.",
        test="Two parts: mean Δ12 ln(credit) > 0, and mean [Δ12 ln(UPI total) − Δ12 ln(credit)] > 0. "
             + expansion_text(gap) + ".",
        estimate=f"Credit {drift_line(dc, 'growth')[0]}; UPI minus credit {drift_line(dg)[0]}",
        consistency=f"Credit up in {dc['pos']} of {dc['n']} months; UPI faster in {dg['pos']} of {dg['n']}",
        p_value=max(p_two_sided(dc["est"], dc["se"]), p_two_sided(dg["est"], dg["se"])),
        ci=[dg["lo"], dg["hi"]], point=dg["est"], verdict=combine(parts),
        components=[{"name": "Credit grows", "verdict": parts[0]},
                    {"name": "UPI grows faster than credit", "verdict": parts[1]}],
        chart=chart("lines", "UPI share of UPI + card payments (volume)", s["months"],
                    [("Total UPI share (upper bound)", [None if v is None else 100 * v for v in s["upi_share_cards"]]),
                     ("UPI P2M share (from Apr 2020)", [None if v is None else 100 * v for v in s["p2m_share_cards"]])],
                    "%", markers=MARKERS),
        data=f"{CARD_DATA}; NPCI UPI product statistics",
        coverage=yoy_example(gc, "growth", s["months"]),
        derivation=derivation([
            drift_step("Was credit-card payment volume growing?", dc, ">0", r"\hat\mu_{C}", "growth", s["months"]),
            drift_step("Was total UPI growing faster than credit cards?", dg, ">0", r"\hat\mu_{U-C}", months=s["months"])],
            combine(parts)),
        formulas=drift_formulas(D12 + r"\ln N^{C}_t", ">0", r"N^{C}_t = \text{credit-card payments},\ U_t = \text{total UPI volume}", tag="C")
        + drift_formulas(D12 + r"\ln U_t - " + D12 + r"\ln N^{C}_t", ">0", tag="U-C"))


def e3(s):
    gap = sub(expansion_growth(s, "debit_n"), expansion_growth(s, "credit_n"))
    share = [d / (d + c) if d and c else None for d, c in zip(s["debit_n"], s["credit_n"])]
    return drift_hypothesis(
        "E3", GROUP, "Debit lost ground to credit during the expansion",
        "Re-test of S1 on the expansion phase: debit-card payments grew more slowly than credit-card payments.",
        "Debit is the closer substitute for account-funded UPI, so it should lose ground first.",
        "Mean of [Δ12 ln(debit) − Δ12 ln(credit)]", "<0", gap, "log",
        chart("lines", "Debit share of card payments (volume), spliced", s["months"],
              [("Debit share of cards", [None if v is None else 100 * v for v in share])], "%", markers=MARKERS),
        CARD_DATA, D12 + r"\ln N^{D}_t - " + D12 + r"\ln N^{C}_t",
        r"N^{D}_t,\ N^{C}_t = \text{debit- and credit-card payments (spliced)}",
        question="Did debit volume grow more slowly than credit volume during the expansion phase?",
        test_text=expansion_text(gap), months=s["months"])


def e4(s):
    g = expansion_growth(s, "imps_n")
    return drift_hypothesis(
        "E4", GROUP, "IMPS co-expanded with UPI",
        "IMPS volume grew year on year in Apr 2017 – Feb 2020 while UPI took share (relative share loss only).",
        "Before UPI's network covered most transfer use cases, the older rail kept growing; H7 dates its turn "
        "to contraction much later.",
        "Mean of Δ12 ln(IMPS volume)", ">0", g, "growth",
        chart("lines", "UPI share of transfer basket (UPI + IMPS volume)", s["months"],
              [("UPI share", [None if v is None else 100 * v for v in s["upi_share_transfer"]])], "%",
              markers=MARKERS),
        "NPCI IMPS and UPI product statistics", D12 + r"\ln I_t", r"I_t = \text{IMPS volume}",
        question="Was IMPS volume higher than a year earlier during the expansion phase?",
        test_text=expansion_text(g), months=s["months"])


LONG_REGISTER = [e1, e2, e3, e4]


def p_text(p: float) -> str:
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def break_rows(s, tests) -> list[dict[str, object]]:
    """The break registry for the docs, with how each break is handled and what the tests show."""
    by = {b: [t for t in tests if t["break"] == b] for b in ("SA", "MDR")}
    mdr = {t["series"].split(" (")[0]: t for t in by["MDR"]}
    covid = mdr["Debit-card payments"]
    split = s["splice"]["splitConsistency"]
    handling = {
        "DM": "Excluded by design: break-model windows start after it, and expansion-phase tests use "
              "year-on-year changes from Apr 2018, so no comparison has a demonetisation or pre-demonetisation base.",
        "SA": "Known-date level-shift test, Aug 2017 – Jul 2019 (±12 months, quadratic adoption trend), "
              "with IMPS as a placebo; E2 is also reported without the UPI comparisons that straddle Aug 2018.",
        "MDR": "Known-date level + slope break test on Apr 2017 – Mar 2023 with COVID month dummies; the "
               "debit ÷ credit ratio nets shocks common to both card types (credit kept its MDR).",
        "CV": "One dummy per month Mar – Dec 2020 and Apr – Jun 2021 in break models (equivalent to dropping "
              "them); expansion-phase tests end Feb 2020.",
        "SP": "Sample boundary, not a level break: P2M comparisons start Apr 2020; "
              f"P2P + P2M volume equals the UPI product volume (within 0.11 mn) in {split['matching']} of "
              f"{split['months']} months (exceptions: {', '.join(label_month(m) for m in split['exceptions']) or 'none'}).",
        "RC": "Direct test: the two sources overlap for nine months (see the splice table). A level dummy from "
              "Jun 2021 in the card break models is a weaker check, because that month also ends the second wave.",
    }
    findings = {
        "DM": "Excluded by design, not tested; E1 is also reported with the earlier comparisons included.",
        "SA": "; ".join(f"{t['series']}: level {t['levelPct']:+.1f}%, Wald {p_text(t['p'])}" for t in by["SA"])
              + ". The placebo also rejects, so a 24-month window cannot isolate the exclusion; no visible drop in UPI.",
        "MDR": "; ".join(f"{t['series']}: slope change {t['slopePp']:+.1f} log pts/yr, level {t['levelPct']:+.1f}%, "
                         f"Wald {p_text(t['p'])}" for t in by["MDR"])
               + ". Zero MDR on RuPay debit did not protect debit; the date also precedes COVID and P2M scale-up.",
        "CV": "; ".join(
            f"{name}: {100 * (math.exp(t['covidMean']) - 1):+.0f}% vs the fitted path on average in Mar – Dec 2020, "
            f"trough {100 * (math.exp(t['covidTrough']) - 1):+.0f}% in {label_month(t['covidTroughMonth'])}"
            for name, t in (("Debit", covid), ("Credit", mdr["Credit-card payments"]))) + ".",
        "SP": f"P2M first observed {label_month(split['first'])}; P2M share of UPI volume then "
              f"{100 * s['p2m_n'][s['months'].index(split['first'])] / s['upi_n'][s['months'].index(split['first'])]:.0f}%.",
        "RC": (f"Overlap ratios PSI ÷ bank-wise {min(f['overlapMin'] for f in s['splice']['factors']):.3f} – "
               f"{max(f['overlapMax'] for f in s['splice']['factors']):.3f}; out-of-sample error at most "
               f"{max(f['checkMax'] for f in s['splice']['factors']):.1f}%. Break-model step from Jun 2021: "
               + "; ".join(f"{t['series'].split(' (')[0]} {100 * (math.exp(t['splice']) - 1):+.1f}% "
                           f"(SE {100 * t['spliceSe']:.1f})" for t in by["MDR"] if "splice" in t)
               + " — read as post-wave recovery as much as a bridge error."),
    }
    rows = []
    for b in BREAKS:
        in_primary = b["month"] >= "2021-06"
        rows.append({**b, "handling": handling[b["id"]], "finding": findings[b["id"]],
                     "primaryWindow": ("Splice point = first month of the primary sample" if b["id"] == "RC" else
                                       "Inside: only the base month Jun 2021 (wave 2) — see robustness"
                                       if b["id"] == "CV" else
                                       "Inside" if in_primary else "Before the primary sample (outside)")})
    return rows


def write_outputs(s, tests) -> None:
    OUTPUT.mkdir(exist_ok=True)
    keys = ["upi_n", "imps_n", "p2m_n", "debit_n", "debit_v", "credit_n", "credit_v",
            "debit_n_bank", "debit_n_psi", "credit_n_bank", "credit_n_psi"]
    with (OUTPUT / "long_series_panel.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["month", "card_source"] + keys)
        for i, m in enumerate(s["months"]):
            source = "RBI PSI" if m >= SPLICE_START else "RBI bank-wise total x k"
            writer.writerow([m, source] + ["" if s[k][i] is None else round(s[k][i], 3) for k in keys])
    with (OUTPUT / "structural_breaks.csv").open("w", newline="", encoding="utf-8") as stream:
        fields = ["break", "series", "window", "n", "k", "quadratic", "seasonal", "trendPp", "levelPct", "slopePp",
                  "wald", "df", "p", "covidMean", "covidTroughMonth", "covidTrough", "splice", "spliceSe"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for t in tests:
            writer.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in t.items()})


def run() -> dict[str, object]:
    s = load()
    tests = break_tests(s)
    write_outputs(s, tests)
    hyps = [build(s) for build in LONG_REGISTER]
    debit = next(t for t in tests if t["series"].startswith("Debit-card"))
    early = drift(expansion_growth(s, "debit_n", window=("2017-04", EXPANSION[1])))
    hyps[0]["note"] = (f"Break model check (Apr 2017 – Mar 2023, month and COVID dummies): "
                       f"debit trend before Jan 2020 {debit['trendPp']:+.1f} log points a year "
                       f"[95% CI {100 * (debit['trend'] - Z95 * debit['trendSe']):+.1f}, "
                       f"{100 * (debit['trend'] + Z95 * debit['trendSe']):+.1f}], change after Jan 2020 "
                       f"{debit['slopePp']:+.1f} log points a year. Including the Apr 2017 – Mar 2018 comparisons "
                       f"against pre-demonetisation bases: {drift_line(early, 'growth')[0]} "
                       f"({verdict(early['lo'], early['hi'], '<0')}).")
    tests_out = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in t.items()} for t in tests]
    return {"hypotheses": hyps, "breaks": break_rows(s, tests), "break_tests": tests_out,
            "splice": s["splice"]}


if __name__ == "__main__":
    result = run()
    for t in result["break_tests"]:
        slope = "" if t["slopePp"] is None else f"slope {t['slopePp']:+7.2f}"
        print(f"{t['break']:4s} {t['series']:48s} level {t['levelPct']:+7.2f}% {slope:14s} p {t['p']:.3f}")
    for f in result["splice"]["factors"]:
        print(f)
    for h in result["hypotheses"]:
        print(f"{h['id']:4s} {h['verdict']:20s} {h['title']}\n     {h['estimate']}")
