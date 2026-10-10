#!/usr/bin/env python3
"""Test bank-, app- and state-level hypotheses about the changing payment landscape.

Run from any directory with: python3 analysis/landscape_tests.py
Inputs are the annual CSVs written by scripts/import_landscape_sources.py:
  rbi_bankwise_cards.csv, npci_upi_member_banks.csv, npci_upi_apps.csv,
  npci_upi_states.csv, phonepe_pulse_states.csv (all under by_year/<YYYY>/).
Outputs: analysis/output/landscape_hypotheses.csv and docs/landscape-data.js, which
feeds docs/landscape.html. Standard library only; estimators and verdict rules are
shared with analysis/hypothesis_tests.py.

These are supporting analyses: they ask whether the national pattern in the main
study holds across banks, UPI apps and states. They are associations in published
aggregates, not causal estimates.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hypothesis_tests import (  # noqa: E402
    D12, Z95, check_text, combine, derivation, diff, drift, drift_formulas, drift_line,
    drift_step, hac_se, hypothesis, label_month, ln, logodds, ols, p_two_sided, pct, rounded,
    sub, verdict, yoy_example,
)
from payment_mix_model import END_MONTH, OUTPUT, REPO, month_range  # noqa: E402

DOCS_DATA = REPO / "docs" / "landscape-data.js"
BANK_START = "2022-03"           # first month of RBI's 26-column bank-wise layout
APP_START = "2022-01"
# Same start as the study's merchant window; Pulse category mixes before 2022 are erratic.
PULSE_START, PULSE_END = "2022-Q1", "2026-Q1"
PULSE_LONG_START = "2018-Q1"
FY_BASE = month_range("2022-04", "2023-03")
FY_LATEST = month_range("2025-04", "2026-03")
TOP_BANKS = 20
CITI = "CITI BANK"
PAYTM_ORDER = "2024-02"          # first full month after RBI's 31 Jan 2024 order on Paytm Payments Bank
LEADERS = ("PhonePe", "Google Pay")
QUARTER_LAGS = 4

RBI_BANKS = "RBI Bank-wise ATM/PoS/Card Statistics (monthly, Mar 2022–Mar 2026)"
NPCI_APPS = "NPCI UPI ecosystem statistics: UPI Applications"
NPCI_MEMBERS = "NPCI UPI ecosystem statistics: Top 50 member banks (volume)"
NPCI_STATES = "NPCI UPI ecosystem statistics: UPI state-wise statistics"
PULSE = "PhonePe Pulse open data (CDLA-Permissive-2.0), state transaction counts by category"


# ---------- Loading ----------

def read_long(filename: str) -> list[dict[str, str]]:
    rows = []
    for path in sorted((REPO / "by_year").glob(f"*/{filename}")):
        with path.open(encoding="utf-8", newline="") as stream:
            rows += list(csv.DictReader(stream))
    if not rows:
        raise SystemExit(f"No {filename} found. Run python3 scripts/import_landscape_sources.py first.")
    return rows


def quarters(start: str, end: str) -> list[str]:
    y, q, out = int(start[:4]), int(start[-1]), []
    while f"{y}-Q{q}" <= end:
        out.append(f"{y}-Q{q}")
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def load() -> dict[str, object]:
    s: dict[str, object] = {"bank_months": month_range(BANK_START, END_MONTH),
                            "app_months": month_range(APP_START, END_MONTH),
                            "quarters": quarters(PULSE_START, PULSE_END)}
    banks: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    group: dict[str, str] = {}
    for r in read_long("rbi_bankwise_cards.csv"):
        values = {k: float(v) for k, v in r.items() if k not in ("month", "bank", "bank_name_as_published", "bank_group")}
        values["debit_n"] = values["debit_pos_n"] + values["debit_online_n"] + values["debit_other_n"]
        values["credit_n"] = values["credit_pos_n"] + values["credit_online_n"] + values["credit_other_n"]
        banks[r["bank"]][r["month"]] = values
        group[r["bank"]] = r["bank_group"]
    s["banks"], s["bank_group"] = banks, group

    upi_banks: dict[str, dict[str, float]] = defaultdict(dict)
    for r in read_long("npci_upi_member_banks.csv"):
        upi_banks[r["bank"]][r["month"]] = float(r["volume_mn"])
    s["upi_banks"] = upi_banks

    apps: dict[str, dict[str, float]] = defaultdict(dict)
    for r in read_long("npci_upi_apps.csv"):
        apps[r["month"]][r["app"]] = float(r["volume_mn"])
    s["apps"] = apps

    states: dict[str, dict[str, float]] = defaultdict(dict)
    for r in read_long("npci_upi_states.csv"):
        states[r["month"]][r["state"]] = float(r["volume_mn"])
    s["npci_states"] = states

    pulse: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for r in read_long("phonepe_pulse_states.csv"):
        total = float(r["p2p_count"]) + float(r["utility_count"]) + float(r["merchant_count"])
        pulse[r["quarter"]][r["state"]] = (total, float(r["merchant_count"]))
    s["pulse"] = pulse
    s["long_quarters"] = quarters(PULSE_LONG_START, PULSE_END)
    return s


def top5_share(s, qs):
    totals = defaultdict(float)
    for q in qs[:4]:
        for st, (t, _) in s["pulse"][q].items():
            totals[st] += t
    top5 = sorted(totals, key=totals.get, reverse=True)[:5]
    return top5, [sum(s["pulse"][q][st][0] for st in top5) / sum(t for t, _ in s["pulse"][q].values()) for q in qs]


def national_merchant_share(s, qs):
    return [sum(m for _, m in s["pulse"][q].values()) / sum(t for t, _ in s["pulse"][q].values()) for q in qs]


# ---------- Estimators not in hypothesis_tests ----------

def drift_lags(values, lags):
    """Mean with Newey-West SE at a chosen lag (quarterly series use 4)."""
    clean = [v for v in values if v is not None]
    x = [[1.0] for _ in clean]
    fit = ols(clean, x)
    se = hac_se(fit, x, lags)[0]
    est = fit["beta"][0]
    return {"est": est, "se": se, "lo": est - Z95 * se, "hi": est + Z95 * se, "n": len(clean),
            "neg": sum(v < 0 for v in clean), "pos": sum(v > 0 for v in clean), "values": values}


def hc1(y, x):
    """OLS with heteroskedasticity-robust (HC1) standard errors for cross-sections."""
    fit = ols(y, x)
    n, k, inv = fit["n"], fit["k"], fit["inv"]
    meat = [[sum(e * e * row[i] * row[j] for e, row in zip(fit["resid"], x)) for j in range(k)] for i in range(k)]
    cov = [[sum(inv[i][a] * meat[a][b] * inv[b][j] for a in range(k) for b in range(k)) * n / (n - k)
            for j in range(k)] for i in range(k)]
    fit["se"] = [math.sqrt(max(cov[i][i], 0.0)) for i in range(k)]
    return fit


def sign_test(k, n):
    """One-sided exact binomial p-value for at least k successes in n trials at p = 0.5."""
    return sum(math.comb(n, j) for j in range(k, n + 1)) / 2 ** n


def sign_verdict(k, n):
    if sign_test(k, n) < 0.05:
        return "Supported"
    return "Contradicted" if sign_test(n - k, n) < 0.05 else "Not supported"


ACRONYMS = {"HDFC", "ICICI", "IDBI", "IDFC", "DBS", "AU", "CSB", "DCB", "RBL", "SBM", "UCO", "HSBC", "ESAF",
             "KEB", "NSDL", "PLC", "B.S.C.", "Q.P.S.C.", "SBI"}
SMALL_WORDS = {"OF", "AND", "&"}
SPELLINGS = {"INDUSIND": "IndusInd"}


def nice(name):
    words = [w for w in name.split() if w.rstrip(".") != "LTD"]
    return " ".join(w if w in ACRONYMS else SPELLINGS[w] if w in SPELLINGS else w.lower() if i and w in SMALL_WORDS else w.capitalize()
                    for i, w in enumerate(words))


def share(num, den):
    return [None if d in (None, 0) or n is None else n / d for n, d in zip(num, den)]


def lag_diff(values, lag):
    return diff(values, lag)


def hbar(label, unit, bars):
    return {"kind": "hbar", "label": label, "unit": unit,
            "bars": [{"name": n, "value": round(v, 2), "group": g} for n, v, g in bars]}


def scatter(label, xlabel, ylabel, points, fit=None):
    return {"kind": "scatter", "label": label, "unit": "", "xlabel": xlabel, "ylabel": ylabel,
            "points": [{"name": n, "x": round(x, 4), "y": round(y, 4), "group": g} for n, x, y, g in points],
            "fit": [round(fit[0], 5), round(fit[1], 5)] if fit else None}


def lines(label, months, series, unit, marker=None):
    return {"kind": "lines", "label": label, "months": months, "unit": unit, "marker": marker,
            "series": [{"name": n, "values": rounded(v, 3)} for n, v in series]}


def drift_card(hid, group, title, statement, framework, question, series, direction, months, unit,
               formula, where, chart_spec, data, lags=None, note=None, metric=""):
    d = drift_lags(series, lags) if lags else drift(series)
    estimate, consistency = drift_line(d, unit)
    final = verdict(d["lo"], d["hi"], direction)
    period = "quarter" if lags else "month"
    first = next(m for m, v in zip(months, series) if v is not None)
    test = (f"{metric}. Mean year-on-year log change, Newey-West ({lags or 12}-lag) standard errors, "
            f"{d['n']} {period}s {label_month(first)}–{label_month(months[-1])}. Supported if the 95% "
            f"interval is {'below' if direction == '<0' else 'above'} zero.")
    step = {k: v for k, v in drift_step(question, d, direction, unit=unit, months=months).items() if k != "example"}
    return hypothesis(
        id=hid, group=group, title=title, statement=statement, framework=framework, test=test,
        estimate=estimate, consistency=consistency.replace("year-on-year changes", f"year-on-year {period}ly changes"),
        p_value=p_two_sided(d["est"], d["se"]), verdict=final, chart=chart_spec, data=data,
        coverage=yoy_example(series, unit, months),
        formulas=drift_formulas(formula, direction, where), note=note,
        derivation=derivation([step], final))


# ---------- Bank hypotheses (RBI bank-wise, NPCI member banks) ----------

def fy_total(by_month, months, field=None):
    vals = [by_month.get(m) for m in months]
    if any(v is None for v in vals):
        return None
    return sum(v[field] if field else v for v in vals)


def top_debit_banks(s):
    base = {b: fy_total(m, FY_BASE, "debit_n") for b, m in s["banks"].items()}
    base = {b: v for b, v in base.items() if v and fy_total(s["banks"][b], FY_LATEST, "debit_n")}
    return sorted(base, key=base.get, reverse=True)


def b1(s):
    ranked = top_debit_banks(s)[:TOP_BANKS]
    changes = []
    for b in ranked:
        start, end = fy_total(s["banks"][b], FY_BASE, "debit_n"), fy_total(s["banks"][b], FY_LATEST, "debit_n")
        changes.append((b, (math.log(end) - math.log(start)) / 3))
    k = sum(c < 0 for _, c in changes)
    p = sign_test(k, len(changes))
    part1 = sign_verdict(k, len(changes))
    months = s["bank_months"]
    rest = [sum(v[m]["debit_n"] for b, v in s["banks"].items() if b != "STATE BANK OF INDIA" and m in v) for m in months]
    g = diff(ln(rest))
    d = drift(g)
    part2 = verdict(d["lo"], d["hi"], "<0")
    final = combine([part1, part2])
    sign_step = {
        "question": f"Of the top {TOP_BANKS} debit-card issuers, do significantly more than half show a fall?",
        "calc": (r"k = \#\{i : c_i < 0\} = " + str(k) + r",\quad n = " + str(len(changes))
                 + r",\quad p = \Pr(K \ge " + str(k) + r" \mid \text{fair coin}) = " + f"{p:.2g}"),
        "check": (f"{k} of {len(changes)} banks fell between FY2022-23 and FY2025-26; "
                  f"p = {p:.2g} is {'below' if p < 0.05 else 'not below'} 0.05."),
        "verdict": part1,
        "example": "; ".join(f"{nice(b)} {pct(c)}/yr" for b, c in changes[:5]) + " (the five largest issuers).",
    }
    return hypothesis(
        id="B1", group="Banks", title="The debit-card decline is broad-based across banks",
        statement=f"Debit-card payments fall at most of the {TOP_BANKS} largest issuing banks, and fall even "
                  "after excluding State Bank of India (the largest issuer).",
        framework="If debit and UPI are close substitutes for every account holder, the decline should not "
                  "depend on one bank's customers, reporting changes or card-portfolio clean-ups.",
        test=f"Two parts. (1) Sign test: annualised change in each bank's debit-card payments, FY2022-23 to "
             f"FY2025-26, for the {TOP_BANKS} largest issuers in FY2022-23; supported if one-sided binomial "
             "p < 0.05. (2) Drift test on all other banks' combined monthly debit payments (excluding SBI), "
             "Newey-West (12-lag) SE.",
        estimate=f"{k} of {len(changes)} banks fell (p = {p:.2g}); excluding SBI: {drift_line(d, 'growth')[0]}",
        consistency=drift_line(d)[1], p_value=None, verdict=final,
        components=[{"name": "Most large banks fall", "verdict": part1},
                    {"name": "Falls excluding SBI", "verdict": part2}],
        coverage=("Part 1 compares full financial years (Apr 2022–Mar 2023 vs Apr 2025–Mar 2026), so it "
                  "is a three-year change per bank, annualised. Part 2 averages "
                  f"{d['n']} year-on-year monthly changes, {label_month(months[12])} to {label_month(months[-1])}."),
        derivation=derivation([sign_step, drift_step("Excluding SBI, do the other banks' debit payments fall?",
                                                     d, "<0", r"\hat\mu_{-SBI}", "growth", months)], final),
        formulas=[("Bank change", r"c_i = \tfrac{1}{3}\big[\ln D_{i,\mathrm{FY26}} - \ln D_{i,\mathrm{FY23}}\big]"),
                  ("where", r"D_{i,\mathrm{FY}} = \text{debit-card payments (PoS + online + other) of bank } i \text{ in the financial year}"),
                  ("Sign test", r"k = \#\{i : c_i < 0\},\qquad p = \sum_{j=k}^{n}\binom{n}{j}0.5^{n}"),
                  ("Supported if", r"p < 0.05")]
        + drift_formulas(D12 + r"\ln \textstyle\sum_{i \ne \mathrm{SBI}} D_{it}", "<0", tag="-SBI"),
        chart=hbar(f"Top {TOP_BANKS} debit issuers: annualised change in debit-card payments, FY2022-23 to FY2025-26",
                   "% a year", [(nice(b), 100 * (math.exp(c) - 1), s["bank_group"][b]) for b, c in sorted(changes, key=lambda t: t[1])]),
        data=RBI_BANKS)


def group_series(s, field, groups, months, per=None):
    out = []
    for m in months:
        num = sum(v[m][field] for b, v in s["banks"].items() if s["bank_group"][b] in groups and m in v)
        if per:
            den = sum(v[m][per] for b, v in s["banks"].items() if s["bank_group"][b] in groups and m in v)
            out.append(num / den if den else None)
        else:
            out.append(num)
    return out


def b2(s):
    months = s["bank_months"]
    psb = group_series(s, "debit_n", {"Public sector"}, months, "debit_cards")
    pvt = group_series(s, "debit_n", {"Private sector"}, months, "debit_cards")
    g = sub(diff(ln(psb)), diff(ln(pvt)))
    return drift_card(
        "B2", "Banks", "Public-sector banks' debit use per card falls faster",
        "Debit-card payments per card in force fall faster at public-sector banks than at private-sector banks.",
        "Public-sector banks issue most RuPay and mass-market debit cards, whose holders make many small "
        "payments — the payments UPI replaces first. Private-bank customers use cards more for larger and "
        "online purchases.",
        "Do payments per debit card shrink faster at public-sector banks than at private banks?",
        g, "<0", months, "log",
        D12 + r"\ln u^{\mathrm{PSB}}_t - " + D12 + r"\ln u^{\mathrm{PVT}}_t",
        r"u^{G}_t = \frac{\sum_{i \in G} D_{it}}{\sum_{i \in G} K_{it}},\quad K_{it} = \text{debit cards in force of bank } i",
        lines("Debit-card payments per card in force, per month", months,
              [("Public sector", psb), ("Private sector", pvt)], "payments per card per month"),
        RBI_BANKS, metric="Mean of [Δ12 ln(PSB payments per card) − Δ12 ln(private payments per card)]",
        note=change_note(s, months))


def change_note(s, months):
    parts = []
    for g, label in (("Public sector", "public-sector"), ("Private sector", "private")):
        pay = group_series(s, "debit_n", {g}, months)
        cards = group_series(s, "debit_cards", {g}, months)
        parts.append(f"{label} banks' debit payments {pct(math.log(pay[-1] / pay[0]))} and cards in force "
                     f"{pct(math.log(cards[-1] / cards[0]))}")
    return (f"From {label_month(months[0])} to {label_month(months[-1])}: " + "; ".join(parts)
            + ". Per-card use depends on card issuance as well as substitution.")


def b3(s):
    months = s["bank_months"]
    total = group_series(s, "credit_n", {"Public sector", "Private sector", "Foreign", "Small finance", "Payments bank"}, months)
    citi = [s["banks"].get(CITI, {}).get(m, {}).get("credit_n") or 0.0 for m in months]
    pvt = [p + c for p, c in zip(group_series(s, "credit_n", {"Private sector"}, months), citi)]
    psb = group_series(s, "credit_n", {"Public sector"}, months)
    sh = share(pvt, total)
    return drift_card(
        "B3", "Banks", "Credit-card payments concentrate in private banks",
        "Private-sector banks' share of credit-card payment volume rises.",
        "Credit is the imperfect substitute that survives UPI (credit period, rewards). Private banks "
        "compete hardest on rewards and co-branded cards, so the surviving card business should tilt toward them.",
        "Is the private-bank share of credit-card payments (in log-odds) rising?",
        diff(logodds(sh)), ">0", months, "log",
        D12 + r"\,\mathrm{logit}\, s_t",
        r"s_t = \frac{C^{\mathrm{PVT}}_t}{C_t},\quad C = \text{credit-card payments (PoS + online + other)},\quad \mathrm{logit}\, s = \ln\frac{s}{1-s}",
        lines("Share of credit-card payment volume by bank group", months,
              [("Private sector", [100 * v for v in sh]), ("Public sector", [100 * p / t for p, t in zip(psb, total)]),
               ("Foreign and others", [100 * (1 - (p + q) / t) for p, q, t in zip(pvt, psb, total)])], "%"),
        RBI_BANKS, metric="Mean of Δ12 log-odds(private-bank share of credit-card payments)",
        note=("Citibank India's consumer card business moved to Axis Bank in March 2023, so Citibank is counted "
              "with the private banks throughout; otherwise the transfer shows up as a one-off jump in the "
              "private share."))


def b4(s):
    pairs = []
    for b in top_debit_banks(s):
        upi = s["upi_banks"].get(b, {})
        u0, u1 = fy_total(upi, FY_BASE), fy_total(upi, FY_LATEST)
        k0, k1 = fy_total(s["banks"][b], FY_BASE, "debit_cards"), fy_total(s["banks"][b], FY_LATEST, "debit_cards")
        d0, d1 = fy_total(s["banks"][b], FY_BASE, "debit_n"), fy_total(s["banks"][b], FY_LATEST, "debit_n")
        if None in (u0, u1, k0, k1) or min(u0, u1, k0, k1, d0, d1) <= 0:
            continue
        pairs.append((b, (math.log(u1) - math.log(u0)) / 3, (math.log(d1) - math.log(d0)) / 3,
                      (math.log(k1) - math.log(k0)) / 3))
    y = [p[2] for p in pairs]
    x = [[1.0, p[1], p[3]] for p in pairs]
    fit = hc1(y, x)
    b, se = fit["beta"][1], fit["se"][1]
    lo, hi = b - Z95 * se, b + Z95 * se
    final = verdict(lo, hi, "<0")
    step = {"question": "Across banks, did those whose customers' UPI payments grew faster lose debit-card use faster?",
            "calc": (r"\hat b = " + f"{b:+.3f}" + r",\quad \widehat{\mathrm{SE}}_{\mathrm{HC1}} = " + f"{se:.3f}"
                     + r",\quad \text{95\% interval} = [" + f"{lo:+.3f},\\ {hi:+.3f}]" + r",\quad n = " + str(len(pairs))),
            "check": check_text(lo, hi, "<0"), "verdict": final}
    simple = ols(y, [[1.0, p[1]] for p in pairs])
    return hypothesis(
        id="B4", group="Banks", title="Banks whose customers adopt UPI faster lose debit use faster",
        statement="Across banks, faster growth in UPI payments sent from a bank's accounts goes with a faster "
                  "decline in that bank's debit-card payments, holding the growth of its card base fixed.",
        framework="Within a bank, debit card and UPI draw on the same account balance. If they are close "
                  "substitutes, the banks whose customers shift most to UPI should see the steepest card decline.",
        test="Cross-section of banks present in both RBI bank-wise card data and NPCI's top-50 UPI member list "
             "in every month of FY2022-23 and FY2025-26. OLS with heteroskedasticity-robust (HC1) SE. "
             "Supported if b < 0 with the 95% interval below zero.",
        estimate=f"b = {b:+.3f} [95% CI {lo:+.3f}, {hi:+.3f}], n = {len(pairs)} banks",
        consistency=f"Without the card-base control, b = {simple['beta'][1]:+.3f}",
        p_value=p_two_sided(b, se), verdict=final,
        coverage=("One observation per bank: its annualised change between full financial years FY2022-23 "
                  "(Apr 2022–Mar 2023) and FY2025-26 (Apr 2025–Mar 2026). b is the average link across banks, "
                  "not a time-series result."),
        note=("NPCI counts UPI volume by member bank (the bank whose account sends the payment, as published "
              "for the top 50 members). Bank-level associations can reflect customer mix, mergers or app "
              "partnerships, so they are not causal estimates."),
        derivation=derivation([step], final),
        formulas=[("Regression", r"c^{D}_i = a + b\,c^{U}_i + \gamma\,c^{K}_i + e_i"),
                  ("where", r"c^{X}_i = \tfrac{1}{3}\big[\ln X_{i,\mathrm{FY26}} - \ln X_{i,\mathrm{FY23}}\big],\quad "
                            r"D = \text{debit payments},\ U = \text{UPI payments sent},\ K = \text{debit cards in force}"),
                  ("Hypothesis", r"H_0:\ b = 0 \qquad H_1:\ b < 0"),
                  ("Supported if", r"\hat b + 1.96\,\widehat{\mathrm{SE}}_{\mathrm{HC1}}(\hat b) < 0")],
        chart=dict(scatter("Banks: UPI growth vs debit-card growth, FY2022-23 to FY2025-26 (annualised log change)",
                           "UPI payments sent, log change a year", "Debit-card payments, log change a year",
                           [(nice(p[0]), p[1], p[2], s["bank_group"][p[0]]) for p in pairs],
                           (simple["beta"][0], simple["beta"][1])),
                   unit=f"Dashed line: simple fit without the card-base control (slope {simple['beta'][1]:+.3f}); "
                        f"the tested b = {b:+.3f} holds card-base growth fixed"),
        data=f"{RBI_BANKS}; {NPCI_MEMBERS}")


# ---------- UPI app hypotheses ----------

def app_shares(s, names):
    months = s["app_months"]
    out = []
    for m in months:
        apps = s["apps"].get(m, {})
        total = sum(apps.values())
        out.append(sum(apps.get(n, 0.0) for n in names) / total if total else None)
    return out


def hhi_series(s):
    out = []
    for m in s["app_months"]:
        apps = s["apps"][m]
        total = sum(apps.values())
        out.append(sum((100 * v / total) ** 2 for v in apps.values()))
    return out


def p1(s):
    months = s["app_months"]
    top2 = app_shares(s, LEADERS)
    return drift_card(
        "P1", "UPI apps", "The two leading apps gain share",
        "PhonePe and Google Pay's combined share of UPI transactions rises.",
        "Two-sided network effects favour the largest apps: more users attract more merchants and "
        "integrations, which attract more users. NPCI's proposed 30% per-app cap has not been enforced.",
        "Is the leaders' combined share (in log-odds) higher than a year earlier, on average?",
        diff(logodds(top2)), ">0", months, "log",
        D12 + r"\,\mathrm{logit}\, s^{\mathrm{top2}}_t",
        r"s^{\mathrm{top2}}_t = \frac{N^{\mathrm{PhonePe}}_t + N^{\mathrm{GPay}}_t}{\sum_a N^{a}_t},\quad N^{a}_t = \text{UPI volume via app } a",
        lines("UPI app shares of transaction volume", months,
              [("PhonePe", [100 * v for v in app_shares(s, ["PhonePe"])]),
               ("Google Pay", [100 * v for v in app_shares(s, ["Google Pay"])]),
               ("Paytm", [100 * v for v in app_shares(s, ["Paytm"])]),
               ("All other apps", [100 * (1 - v) for v in app_shares(s, ["PhonePe", "Google Pay", "Paytm"])])], "%"),
        NPCI_APPS, metric="Mean of Δ12 log-odds(PhonePe + Google Pay share of UPI volume)",
        note="Shares are of the sum over all listed apps, which is within about 2% of total UPI volume.")


def p2(s):
    months = s["app_months"]
    paytm = app_shares(s, ["Paytm"])
    top2 = app_shares(s, LEADERS)
    tau = months.index(PAYTM_ORDER)
    y = [math.log(v) for v in paytm]
    x = [[1.0, float(t), 1.0 if t >= tau else 0.0, float(t - tau) if t >= tau else 0.0] for t in range(len(y))]
    fit = ols(y, x)
    se = hac_se(fit, x)
    delta, sd = fit["beta"][2], se[2]
    lo, hi = delta - Z95 * sd, delta + Z95 * sd
    part1 = verdict(lo, hi, "<0")
    pre = [months.index(m) for m in ("2023-11", "2023-12", "2024-01")]
    post = [months.index(m) for m in ("2024-04", "2024-05", "2024-06")]

    def avg(series, idx):
        return sum(series[i] for i in idx) / len(idx)

    lost = avg(paytm, pre) - avg(paytm, post)
    gained = avg(top2, post) - avg(top2, pre)
    capture = gained / lost if lost > 0 else float("nan")
    part2 = "Supported" if capture > 0.5 else "Not supported"
    final = combine([part1, part2])
    return hypothesis(
        id="P2", group="UPI apps", title="Paytm's lost share went to the two leaders",
        statement="After RBI's 31 January 2024 action on Paytm Payments Bank, Paytm's UPI share dropped, and "
                  "most of the lost share went to PhonePe and Google Pay rather than to smaller apps.",
        framework="With strong network effects, users leaving a shocked platform move to the biggest "
                  "alternatives, so a shock to the number three entrenches the top two.",
        test=f"(1) Known-date break in ln(Paytm share) at {label_month(PAYTM_ORDER)} with separate pre/post "
             "trends; Newey-West (12-lag) SE; supported if the level shift δ < 0 with its 95% interval below "
             "zero. (2) Share capture = gain in leaders' share ÷ loss in Paytm's share, Nov 2023–Jan 2024 "
             "average vs Apr–Jun 2024 average; supported if above 0.5.",
        estimate=f"Level shift δ = {delta:+.3f} ({pct(delta)}) [95% CI {lo:+.3f}, {hi:+.3f}]; "
                 f"leaders captured {capture:.0%} of Paytm's lost share",
        consistency=f"Paytm {100 * avg(paytm, pre):.1f}% → {100 * avg(paytm, post):.1f}%; "
                    f"PhonePe + Google Pay {100 * avg(top2, pre):.1f}% → {100 * avg(top2, post):.1f}%",
        p_value=p_two_sided(delta, sd), verdict=final,
        components=[{"name": "Paytm share drops at the order", "verdict": part1},
                    {"name": "Leaders capture most of it", "verdict": part2}],
        coverage=(f"Part 1 uses every month {label_month(months[0])}–{label_month(months[-1])} ({len(months)} months). "
                  "Part 2 compares two 3-month averages either side of the order (Nov 2023–Jan 2024 vs Apr–Jun 2024)."),
        note="The capture ratio is a point estimate without a confidence interval; treat it as descriptive.",
        derivation=derivation([
            {"question": f"Did Paytm's share drop in level at {label_month(PAYTM_ORDER)}, beyond its existing trend?",
             "calc": (r"\hat\delta = " + f"{delta:+.3f}" + r"\;(\approx " + pct(delta).replace("%", r"\%")
                      + r"),\quad \widehat{\mathrm{SE}}_{\mathrm{NW}} = " + f"{sd:.3f}" + r",\quad \text{95\% interval} = ["
                      + f"{lo:+.3f},\\ {hi:+.3f}]"),
             "check": check_text(lo, hi, "<0"), "verdict": part1},
            {"question": "Of the share Paytm lost, did more than half go to PhonePe and Google Pay?",
             "calc": (r"\frac{\Delta s^{\mathrm{top2}}}{-\Delta s^{\mathrm{Paytm}}} = \frac{"
                      + f"{100 * gained:.2f}" + r"}{" + f"{100 * lost:.2f}" + r"} = " + f"{capture:.2f}"),
             "check": f"Capture ratio {capture:.2f} is {'above' if capture > 0.5 else 'not above'} 0.5.",
             "verdict": part2}], final),
        formulas=[("Break model", r"\ln s^{\mathrm{Paytm}}_t = \alpha + \beta t + \delta\,\mathbf{1}[t \ge \tau] + \theta\,(t-\tau)\,\mathbf{1}[t \ge \tau] + e_t,\quad \tau = \text{Feb 2024}"),
                  ("Hypothesis", r"H_0:\ \delta = 0 \qquad H_1:\ \delta < 0"),
                  ("Capture", r"\kappa = \frac{\bar s^{\mathrm{top2}}_{\mathrm{post}} - \bar s^{\mathrm{top2}}_{\mathrm{pre}}}{\bar s^{\mathrm{Paytm}}_{\mathrm{pre}} - \bar s^{\mathrm{Paytm}}_{\mathrm{post}}},\qquad \text{supported if } \kappa > 0.5")],
        chart=lines("Paytm and leaders' share of UPI volume", months,
                    [("Paytm", [100 * v for v in paytm]), ("PhonePe + Google Pay", [100 * v for v in top2])],
                    "%", marker=PAYTM_ORDER),
        data=NPCI_APPS)


def p3(s):
    months = s["app_months"]
    rest = [1 - v for v in app_shares(s, ["PhonePe", "Google Pay", "Paytm"])]
    hhi = hhi_series(s)
    return drift_card(
        "P3", "UPI apps", "Smaller apps grow their share",
        "The share of UPI transactions made through apps other than PhonePe, Google Pay and Paytm rises.",
        "Entry by new apps (Navi, super.money, bank and fintech apps) with cashback and credit-on-UPI "
        "offers, and NPCI's push against concentration, should widen the long tail.",
        "Is the share of all other apps (in log-odds) higher than a year earlier, on average?",
        diff(logodds(rest)), ">0", months, "log",
        D12 + r"\,\mathrm{logit}\, s^{\mathrm{rest}}_t",
        r"s^{\mathrm{rest}}_t = 1 - \frac{N^{\mathrm{PhonePe}}_t + N^{\mathrm{GPay}}_t + N^{\mathrm{Paytm}}_t}{\sum_a N^{a}_t}",
        lines("Concentration of UPI apps", months,
              [("Share outside top 3 (%)", [100 * v for v in rest]), ("HHI ÷ 100", [h / 100 for h in hhi])],
              "% and index"),
        NPCI_APPS, metric="Mean of Δ12 log-odds(share of apps outside the top 3)",
        note=f"Herfindahl-Hirschman index (sum of squared % shares): {hhi[0]:,.0f} in {label_month(months[0])}, "
             f"peak {max(hhi):,.0f} in {label_month(months[hhi.index(max(hhi))])}, {hhi[-1]:,.0f} in "
             f"{label_month(months[-1])}. Above 2,500 counts as highly concentrated in standard merger guidelines.")


# ---------- State hypotheses (PhonePe Pulse; NPCI shown descriptively) ----------

def pulse_states(s):
    return sorted({st for q in s["pulse"].values() for st in q})


def r1(s):
    qs = s["quarters"]
    top5, sh = top5_share(s, qs)
    long_top5, long_sh = top5_share(s, s["long_quarters"])
    long_d = drift_lags(lag_diff(logodds(long_sh), 4), QUARTER_LAGS)
    return drift_card(
        "R1", "States", "Digital payments spread beyond the leading states",
        f"The five states with the most PhonePe transactions in {PULSE_START[:4]} hold a falling share of transactions.",
        "Network effects spread geographically: as acceptance and users reach smaller towns and poorer "
        "states, the early leaders' share should fall even as they keep growing.",
        "Is the early leaders' combined share (in log-odds) lower than a year earlier, on average?",
        lag_diff(logodds(sh), 4), "<0", qs, "log",
        r"\Delta_{4}\,\mathrm{logit}\, s^{\mathrm{top5}}_q",
        r"s^{\mathrm{top5}}_q = \frac{\sum_{j \in \mathrm{top5}} T_{jq}}{\sum_j T_{jq}},\quad T_{jq} = \text{PhonePe transactions in state } j,\ \text{quarter } q;\quad \Delta_4 = \text{change on same quarter last year}",
        lines(f"Share of PhonePe transactions in the top 5 states of {PULSE_START[:4]} (" + ", ".join(nice(t) for t in top5) + ")",
              qs, [("Top-5 share", [100 * v for v in sh])], "%"),
        PULSE, lags=QUARTER_LAGS,
        metric="Mean of Δ4 log-odds(top-5 states' share of PhonePe transactions)",
        note="PhonePe handles roughly 45–50% of UPI, so this is one app's footprint; its geographic mix may "
             "differ from Google Pay's or Paytm's. Robustness: starting in 2018 with that year's top 5 gives "
             f"{drift_line(long_d)[0]}.")


def annual_merchant_share(s, year):
    out = {}
    for st in pulse_states(s):
        rows = [s["pulse"].get(f"{year}-Q{q}", {}).get(st) for q in range(1, 5)]
        if None in rows:
            continue
        total, merchant = sum(r[0] for r in rows), sum(r[1] for r in rows)
        if total > 0 and 0 < merchant < total:
            out[st] = merchant / total
    return out


def r2(s):
    m22, m25 = annual_merchant_share(s, 2022), annual_merchant_share(s, 2025)
    states = sorted(set(m22) & set(m25))
    k = sum(m25[st] > m22[st] for st in states)
    p = sign_test(k, len(states))
    part1 = sign_verdict(k, len(states))
    qs = s["quarters"]
    national = national_merchant_share(s, qs)
    d = drift_lags(lag_diff(logodds(national), 4), QUARTER_LAGS)
    long_d = drift_lags(lag_diff(logodds(national_merchant_share(s, s["long_quarters"])), 4), QUARTER_LAGS)
    part2 = verdict(d["lo"], d["hi"], ">0")
    final = combine([part1, part2])
    bars = sorted(((nice(st), 100 * (m25[st] - m22[st]), "rise" if m25[st] > m22[st] else "fall") for st in states),
                  key=lambda t: t[1])
    return hypothesis(
        id="R2", group="States", title="Merchant payments gain share in most states",
        statement="Merchant payments' share of PhonePe transactions rises between 2022 and 2025 in most states, "
                  "not only in the leading ones.",
        framework="This is the state-level version of U1 (UPI shifts from transfers to purchases): the "
                  "cross-side network effect should operate wherever QR acceptance spreads.",
        test=f"(1) Sign test across {len(states)} states/UTs: merchant share of transactions in calendar 2025 vs "
             "2022; supported if one-sided binomial p < 0.05. (2) National PhonePe merchant share: mean Δ4 "
             f"log-odds, Newey-West (4-lag) SE, {label_month(qs[4])}–{label_month(qs[-1])}.",
        note=("Robustness: the national test starting in 2018 gives "
              f"{drift_line(long_d)[0]}; Pulse category mixes before 2022 are erratic."),
        estimate=f"{k} of {len(states)} states rose (p = {p:.2g}); national {drift_line(d)[0]}",
        consistency=drift_line(d)[1].replace("year-on-year changes", "year-on-year quarterly changes"),
        p_value=None, verdict=final,
        components=[{"name": "Most states rise", "verdict": part1}, {"name": "National share rises", "verdict": part2}],
        coverage=("Part 1 compares full calendar years 2022 and 2025 for each state. Part 2 averages "
                  f"{d['n']} year-on-year quarterly changes, {label_month(qs[4])} to {label_month(qs[-1])}."),
        derivation=derivation([
            {"question": "Did the merchant share rise in significantly more than half of the states?",
             "calc": (r"k = " + str(k) + r",\quad n = " + str(len(states)) + r",\quad p = " + f"{p:.2g}"),
             "check": f"{k} of {len(states)} states rose; p = {p:.2g} is {'below' if p < 0.05 else 'not below'} 0.05.",
             "verdict": part1,
             "example": "; ".join(f"{n} {v:+.1f} pp" for n, v, _ in bars[-3:][::-1]) + " (largest rises); "
                        + "; ".join(f"{n} {v:+.1f} pp" for n, v, _ in bars[:2]) + " (smallest)."},
            drift_step("Is PhonePe's national merchant share (in log-odds) higher than a year earlier?",
                       d, ">0", r"\hat\mu_{\mathrm{nat}}", "log", qs)], final),
        formulas=[("State share", r"m_{j,y} = \frac{\text{merchant transactions}_{j,y}}{\text{all transactions}_{j,y}}"),
                  ("Sign test", r"k = \#\{j : m_{j,2025} > m_{j,2022}\},\qquad p = \sum_{i=k}^{n}\binom{n}{i}0.5^{n}"),
                  ("Supported if", r"p < 0.05")]
        + drift_formulas(r"\Delta_{4}\,\mathrm{logit}\, m^{\mathrm{nat}}_q", ">0", tag="nat"),
        chart=hbar("Change in merchant share of PhonePe transactions, 2022 to 2025", "percentage points", bars),
        data=PULSE)


def r3(s):
    m22, m25 = annual_merchant_share(s, 2022), annual_merchant_share(s, 2025)
    states = sorted(set(m22) & set(m25))
    x0 = [math.log(m22[st] / (1 - m22[st])) for st in states]
    y = [(math.log(m25[st] / (1 - m25[st])) - x) / 3 for st, x in zip(states, x0)]
    fit = hc1(y, [[1.0, x] for x in x0])
    b, se = fit["beta"][1], fit["se"][1]
    lo, hi = b - Z95 * se, b + Z95 * se
    final = verdict(lo, hi, "<0")
    return hypothesis(
        id="R3", group="States", title="States with low merchant use catch up",
        statement="States where merchant payments were a smaller share of transactions in 2022 see that share "
                  "rise faster by 2025 (convergence).",
        framework="Network effects saturate: once most shops in a state accept QR, merchant share grows more "
                  "slowly, while late-adopting states have the most room to grow.",
        test=f"Cross-section of {len(states)} states/UTs: annualised change in log-odds merchant share, 2022–2025, "
             "on its 2022 level; OLS with HC1 SE. Supported if b < 0 with the 95% interval below zero.",
        estimate=f"b = {b:+.3f} [95% CI {lo:+.3f}, {hi:+.3f}], n = {len(states)}",
        consistency=f"Half-gap closes in about {math.log(2) / -b:.1f} years" if b < 0 else "No catch-up implied",
        p_value=p_two_sided(b, se), verdict=final,
        coverage="One observation per state: the change between calendar years 2022 and 2025, annualised.",
        note="Small UTs have few transactions and noisy shares; the regression is unweighted.",
        derivation=derivation([{
            "question": "Did states that started with a lower merchant share gain faster?",
            "calc": (r"\hat b = " + f"{b:+.3f}" + r",\quad \widehat{\mathrm{SE}}_{\mathrm{HC1}} = " + f"{se:.3f}"
                     + r",\quad \text{95\% interval} = [" + f"{lo:+.3f},\\ {hi:+.3f}]"),
            "check": check_text(lo, hi, "<0"), "verdict": final}], final),
        formulas=[("Regression", r"\tfrac{1}{3}\big[\mathrm{logit}\, m_{j,2025} - \mathrm{logit}\, m_{j,2022}\big] = a + b\,\mathrm{logit}\, m_{j,2022} + e_j"),
                  ("Hypothesis", r"H_0:\ b = 0 \qquad H_1:\ b < 0 \ \text{(catch-up)}"),
                  ("Supported if", r"\hat b + 1.96\,\widehat{\mathrm{SE}}_{\mathrm{HC1}}(\hat b) < 0")],
        chart=dict(scatter("States: starting merchant share vs its growth, 2022 to 2025",
                           "logit merchant share, 2022", "change in logit a year",
                           [(nice(st), xv, yv, "state") for st, xv, yv in zip(states, x0, y)],
                           (fit["beta"][0], b)),
                   unit=f"Dashed line: the fitted regression (slope {b:+.3f})"),
        data=PULSE)


REGISTER = [b1, b2, b3, b4, p1, p2, p3, r1, r2, r3]


# ---------- Distributions (descriptive charts) ----------

def distributions(s):
    bm = s["bank_months"]
    groups = ["Public sector", "Private sector", "Foreign", "Small finance", "Payments bank"]
    total_debit = group_series(s, "debit_n", set(groups), bm)
    bank_lines = lines("Share of debit-card payment volume by bank group", bm,
                       [(g, [100 * a / t for a, t in zip(group_series(s, "debit_n", {g}, bm), total_debit)]) for g in groups[:3]],
                       "%")
    latest = {b: fy_total(v, FY_LATEST, "debit_n") or 0 for b, v in s["banks"].items()}
    tot = sum(latest.values())
    top = sorted(latest, key=latest.get, reverse=True)[:12]
    bank_bar = hbar("Largest debit-card issuers, share of FY2025-26 debit payments", "%",
                    [(nice(b), 100 * latest[b] / tot, s["bank_group"][b]) for b in top])

    am = s["app_months"]
    last = s["apps"][am[-1]]
    total = sum(last.values())
    app_bar = hbar(f"UPI apps, share of volume in {label_month(am[-1])}", "%",
                   [(a, 100 * v / total, "app") for a, v in sorted(last.items(), key=lambda t: t[1], reverse=True)[:12]])
    hhi = hhi_series(s)
    hhi_line = lines("UPI app concentration (Herfindahl-Hirschman index)", am, [("HHI", hhi)], "index (0–10,000)",
                     marker=PAYTM_ORDER)

    nm = sorted(s["npci_states"])
    unclassified = [100 * s["npci_states"][m].get("UNCLASSIFIED", 0) / sum(s["npci_states"][m].values()) for m in nm]
    latest_states = {k: v for k, v in s["npci_states"][nm[-1]].items() if k != "UNCLASSIFIED"}
    st_total = sum(latest_states.values())
    state_bar = hbar(f"States' share of state-classified UPI volume, {label_month(nm[-1])} (NPCI)", "%",
                     [(nice(k), 100 * v / st_total, "state") for k, v in sorted(latest_states.items(), key=lambda t: t[1], reverse=True)[:15]])
    unclassified_line = lines("Share of UPI volume NPCI could not assign to a state", nm,
                              [("Unclassified", unclassified)], "%")
    return [
        {"group": "Banks", "title": "Who issues the debit cards, and who is losing them",
         "text": "RBI's bank-wise statistics cover every scheduled commercial, payments and small finance bank. "
                 "Summed across banks, they match RBI's national debit-card total in every month except "
                 "Oct 2024 (within 1%).",
         "charts": [bank_bar, bank_lines]},
        {"group": "UPI apps", "title": "How concentrated UPI is",
         "text": "NPCI publishes monthly volume for every UPI app. Two apps have handled about four in five "
                 "UPI payments throughout; the dashed line marks RBI's Paytm Payments Bank order.",
         "charts": [app_bar, hhi_line]},
        {"group": "States", "title": "Where UPI is used",
         "text": (f"NPCI's state-wise series starts in {label_month(nm[0])}. A large and rising share of volume "
                  f"is 'unclassified' ({unclassified[0]:.0f}% at the start, {unclassified[-1]:.0f}% in "
                  f"{label_month(nm[-1])}), so NPCI state growth rates are not reliable and the state "
                  "hypotheses below use PhonePe Pulse, which assigns every PhonePe transaction to a state."),
         "charts": [state_bar, unclassified_line]},
    ]


def main() -> None:
    s = load()
    results = [build(s) for build in REGISTER]
    OUTPUT.mkdir(exist_ok=True)
    with (OUTPUT / "landscape_hypotheses.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "group", "title", "verdict", "estimate", "consistency", "test", "data"])
        for r in results:
            writer.writerow([r["id"], r["group"], r["title"], r["verdict"], r["estimate"],
                             r["consistency"], r["test"], r["data"]])
    payload = {"dataThrough": END_MONTH, "hypotheses": results, "distributions": distributions(s)}
    DOCS_DATA.write_text(
        "// Generated by analysis/landscape_tests.py. Do not edit.\n"
        f"window.LANDSCAPE_DATA = Object.freeze({json.dumps(payload, separators=(',', ':'), ensure_ascii=False)});\n",
        encoding="utf-8")
    for r in results:
        print(f"{r['id']:4s} {r['verdict']:20s} {r['title']}\n     {r['estimate']}")


if __name__ == "__main__":
    main()
