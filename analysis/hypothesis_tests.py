#!/usr/bin/env python3
"""Test the study's hypotheses against the monthly series and publish a hypothesis register.

Run from any directory with: python3 analysis/hypothesis_tests.py
Inputs are the annual CSV extracts in ../by_year. No third-party package is needed.

Every hypothesis states a predicted direction. Drift tests use the mean 12-month log change
with Newey-West (Bartlett, 12-lag) standard errors; regressions use the same HAC estimator.
Verdicts: Supported (95% interval entirely on the predicted side), Contradicted (entirely on
the opposite side), Not supported (interval includes zero), Partially supported (only some
parts of a compound hypothesis hold), Pending data (inputs not yet collected).

To add a hypothesis, write a function that returns `hypothesis(...)` and list it in REGISTER.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from payment_mix_model import END_MONTH, OUTPUT, REPO, month_range, read_series  # noqa: E402

DOCS_DATA = REPO / "docs" / "hypotheses-data.js"
MERCHANT_START = "2022-01"
TRANSFER_START = "2016-04"
BREAK_SEARCH_START = "2021-01"
HAC_LAGS = 12
Z95 = 1.959964
# Andrews (1993) asymptotic 5% critical value, two restrictions, 15% trimming.
SUP_F_CRITICAL_5PCT = 11.70
FORECAST_TRAIN_END = "2025-03"


# ---------- Linear algebra and estimators (standard library only) ----------

def invert(matrix: list[list[float]]) -> list[list[float]]:
    n = len(matrix)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("Singular design matrix")
        a[col], a[pivot] = a[pivot], a[col]
        scale = a[col][col]
        a[col] = [v / scale for v in a[col]]
        for r in range(n):
            if r != col and a[r][col]:
                factor = a[r][col]
                a[r] = [v - factor * p for v, p in zip(a[r], a[col])]
    return [row[n:] for row in a]


def ols(y: list[float], x: list[list[float]]) -> dict[str, object]:
    k = len(x[0])
    xtx = [[sum(row[i] * row[j] for row in x) for j in range(k)] for i in range(k)]
    xty = [sum(row[i] * v for row, v in zip(x, y)) for i in range(k)]
    inv = invert(xtx)
    beta = [sum(inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    resid = [v - sum(b * c for b, c in zip(beta, row)) for row, v in zip(x, y)]
    return {"beta": beta, "resid": resid, "inv": inv, "ssr": sum(e * e for e in resid),
            "n": len(y), "k": k}


def hac_se(fit: dict[str, object], x: list[list[float]], lags: int = HAC_LAGS) -> list[float]:
    resid, inv, k = fit["resid"], fit["inv"], fit["k"]
    n = len(resid)
    meat = [[0.0] * k for _ in range(k)]
    for lag in range(min(lags, n - 1) + 1):
        weight = 1.0 if lag == 0 else 1 - lag / (lags + 1)
        for t in range(lag, n):
            c = weight * resid[t] * resid[t - lag]
            for i in range(k):
                for j in range(k):
                    term = x[t][i] * x[t - lag][j]
                    if lag:
                        term += x[t - lag][i] * x[t][j]
                    meat[i][j] += c * term
    cov = [[sum(inv[i][a] * meat[a][b] * inv[b][j] for a in range(k) for b in range(k))
            for j in range(k)] for i in range(k)]
    return [math.sqrt(max(cov[i][i], 0.0)) for i in range(k)]


def drift(values: list[float | None]) -> dict[str, float]:
    """Mean of a series with Newey-West standard error."""
    clean = [v for v in values if v is not None]
    x = [[1.0] for _ in clean]
    fit = ols(clean, x)
    se = hac_se(fit, x)[0]
    est = fit["beta"][0]
    return {"est": est, "se": se, "lo": est - Z95 * se, "hi": est + Z95 * se, "n": len(clean),
            "neg": sum(v < 0 for v in clean), "pos": sum(v > 0 for v in clean)}


def p_two_sided(est: float, se: float) -> float:
    return math.erfc(abs(est / se) / math.sqrt(2)) if se > 0 else 0.0


def verdict(lo: float, hi: float, direction: str) -> str:
    if direction == "<0":
        return "Supported" if hi < 0 else "Contradicted" if lo > 0 else "Not supported"
    return "Supported" if lo > 0 else "Contradicted" if hi < 0 else "Not supported"


def combine(parts: list[str]) -> str:
    if all(p == "Supported" for p in parts):
        return "Supported"
    if any(p == "Supported" for p in parts):
        return "Partially supported"
    return "Contradicted" if all(p == "Contradicted" for p in parts) else "Not supported"


# ---------- Series ----------

def ln(values):
    return [math.log(v) if v and v > 0 else None for v in values]


def diff(values, lag=12):
    return [None if i < lag or values[i] is None or values[i - lag] is None
            else values[i] - values[i - lag] for i in range(len(values))]


def sub(a, b):
    return [None if x is None or y is None else x - y for x, y in zip(a, b)]


def logodds(shares):
    return [math.log(s / (1 - s)) for s in shares]


def month_dummies(months):
    return [[1.0 if int(m[5:7]) == c else 0.0 for c in range(2, 13)] for m in months]


def load() -> dict[str, object]:
    months = month_range(MERCHANT_START, END_MONTH)
    split = read_series("npci_upi_p2p_p2m_transactions.csv", MERCHANT_START)
    rbi = read_series("rbi_psi_card_ppi_own_month.csv", MERCHANT_START)
    infra = read_series("rbi_psi_infrastructure.csv", MERCHANT_START, optional=True)
    cpi = read_series("mospi_cpi_combined_monthly.csv", MERCHANT_START)
    imps = read_series("npci_imps_product_statistics.csv", TRANSFER_START)
    upi = read_series("npci_upi_product_statistics.csv", TRANSFER_START)

    def col(rows, field, scale=1.0, ms=months):
        return [float(rows[m][field]) * scale for m in ms]

    s = {"months": months}
    s["debit_cards_outstanding_lakh"] = (
        col(infra, "debit_cards_outstanding") if infra else None
    )
    s["pos_terminals_lakh"] = col(infra, "pos_terminals") if infra else None
    s["upi_qr_codes_lakh"] = col(infra, "upi_qr_codes") if infra else None
    s["p2m_n"], s["p2m_v"] = col(split, "p2m_volume_mn"), col(split, "p2m_value_crore")
    s["p2p_n"] = col(split, "p2p_volume_mn")
    for rail in ("debit_total", "debit_pos", "debit_other", "credit_total", "credit_pos"):
        s[f"{rail}_n"] = col(rbi, f"{rail}_volume_lakh", 0.1)
        s[f"{rail}_v"] = col(rbi, f"{rail}_value_crore")
    s["cpi"] = col(cpi, "cpi_combined_index_linked_2024")
    base = s["cpi"][-1]
    for rail in ("p2m", "debit_total", "credit_total"):
        s[f"{rail}_ticket_real"] = [v * 10 / n * base / c for v, n, c in
                                    zip(s[f"{rail}_v"], s[f"{rail}_n"], s["cpi"])]
    long_months = month_range(TRANSFER_START, END_MONTH)
    s["long_months"] = long_months
    s["imps_n"] = col(imps, "volume_mn", ms=long_months)
    s["upi_n"] = col(upi, "volume_mn", ms=long_months)
    return s


# ---------- Output helpers ----------

def pct(log_points: float) -> str:
    return f"{100 * (math.exp(log_points) - 1):+.1f}%"


def rounded(values, digits=2):
    return [None if v is None else round(v, digits) for v in values]


def chart(kind, label, months, series, unit, marker=None):
    return {"kind": kind, "label": label, "months": months, "unit": unit, "marker": marker,
            "series": [{"name": n, "values": rounded(v)} for n, v in series]}


def growth_chart(label, months, series):
    return chart("bars" if len(series) == 1 else "lines", label, months,
                 [(n, [None if v is None else 100 * v for v in vals]) for n, vals in series],
                 "12-month change, log points × 100 (≈ %)")


def drift_line(d, unit="log"):
    consistent = d["neg"] if d["est"] < 0 else d["pos"]
    if unit == "growth":
        text = f"{pct(d['est'])} a year [95% CI {pct(d['lo'])}, {pct(d['hi'])}]"
    else:
        text = f"{d['est']:+.3f} log points a year [95% CI {d['lo']:+.3f}, {d['hi']:+.3f}]"
    return text, f"{consistent} of {d['n']} months on the same side of zero"


def hypothesis(**fields):
    fields.setdefault("components", [])
    fields.setdefault("chart", None)
    return fields


DRIFT_TEST = ("Mean 12-month log change, Newey-West (12-lag) standard errors, "
              "39 matched months Jan 2023–Mar 2026")


def drift_hypothesis(hid, group, title, statement, framework, metric, direction, series,
                     unit, chart_spec, data):
    d = drift(series)
    estimate, consistency = drift_line(d, unit)
    return hypothesis(
        id=hid, group=group, title=title, statement=statement, framework=framework,
        test=f"{metric}. {DRIFT_TEST}. Supported if the 95% interval is {'below' if direction == '<0' else 'above'} zero.",
        estimate=estimate, consistency=consistency, p_value=p_two_sided(d["est"], d["se"]),
        ci=[d["lo"], d["hi"]], point=d["est"], verdict=verdict(d["lo"], d["hi"], direction),
        chart=chart_spec, data=data)


# ---------- Hypotheses ----------

RBI_CARDS = "RBI Payment System Indicators: domestic debit/credit card purchases"
NPCI_P2M = "NPCI UPI ecosystem statistics: P2P/P2M"


def h1(s):
    g = diff(ln(s["debit_total_n"]))
    return drift_hypothesis(
        "H1", "Merchant payments", "Debit-card purchases contract in absolute terms",
        "Domestic debit-card purchase volume falls year on year, not just its share.",
        "Debit and account-funded UPI are close substitutes (same bank balance, zero merchant fee "
        "for RuPay debit since Jan 2020); with close substitutes consumers move to a corner solution.",
        "Mean of Δ12 ln(debit volume)", "<0", g, "growth",
        growth_chart("Debit-card purchase volume, 12-month change", s["months"], [("Debit", g)]),
        RBI_CARDS)


def h2(s):
    gc, gp = diff(ln(s["credit_total_n"])), diff(ln(s["p2m_n"]))
    d_credit, d_gap = drift(gc), drift(sub(gp, gc))
    parts = [verdict(d_credit["lo"], d_credit["hi"], ">0"), verdict(d_gap["lo"], d_gap["hi"], ">0")]
    return hypothesis(
        id="H2", group="Merchant payments", title="Credit cards grow but lose share",
        statement="Credit-card purchase volume keeps growing, but more slowly than UPI P2M "
                  "(relative share loss, not contraction).",
        framework="Credit cards are imperfect substitutes for UPI (credit period, rewards), so an "
                  "interior mix survives.",
        test="Two parts: mean Δ12 ln(credit) > 0, and mean [Δ12 ln(P2M) − Δ12 ln(credit)] > 0. "
             + DRIFT_TEST + ".",
        estimate=f"Credit {drift_line(d_credit, 'growth')[0]}; P2M minus credit "
                 f"{drift_line(d_gap)[0]}",
        consistency=f"Credit growth positive in {d_credit['pos']} of {d_credit['n']} months; "
                    f"P2M faster in {d_gap['pos']} of {d_gap['n']}",
        p_value=max(p_two_sided(d_credit["est"], d_credit["se"]), p_two_sided(d_gap["est"], d_gap["se"])),
        ci=[d_gap["lo"], d_gap["hi"]], point=d_gap["est"], verdict=combine(parts),
        components=[{"name": "Credit volume grows", "verdict": parts[0]},
                    {"name": "P2M grows faster than credit", "verdict": parts[1]}],
        chart=growth_chart("12-month volume change", s["months"], [("UPI P2M", gp), ("Credit card", gc)]),
        data=f"{RBI_CARDS}; {NPCI_P2M}")


def h3(s):
    td, tp = diff(ln(s["debit_total_ticket_real"])), diff(ln(s["p2m_ticket_real"]))
    dd, dp = drift(td), drift(tp)
    parts = [verdict(dd["lo"], dd["hi"], ">0"), verdict(dp["lo"], dp["hi"], "<0")]
    return hypothesis(
        id="H3", group="Merchant payments", title="Ticket sorting: UPI takes the small payments",
        statement="The average real debit-card payment rises while the average real UPI P2M payment falls.",
        framework="If UPI wins small, frequent purchases first, the debit payments that remain are larger.",
        test="Two parts: mean Δ12 ln(real debit ticket) > 0 and mean Δ12 ln(real P2M ticket) < 0. "
             + DRIFT_TEST + ". Real = March 2026 rupees (MoSPI CPI).",
        estimate=f"Debit ticket {drift_line(dd, 'growth')[0]}; P2M ticket {drift_line(dp, 'growth')[0]}",
        consistency=f"Debit ticket up in {dd['pos']} of {dd['n']} months; P2M ticket down in "
                    f"{dp['neg']} of {dp['n']}",
        p_value=max(p_two_sided(dd["est"], dd["se"]), p_two_sided(dp["est"], dp["se"])),
        ci=[dd["lo"], dd["hi"]], point=dd["est"], verdict=combine(parts),
        components=[{"name": "Debit ticket rises", "verdict": parts[0]},
                    {"name": "P2M ticket falls", "verdict": parts[1]}],
        chart=chart("lines", "Average real ticket, March 2026 rupees", s["months"],
                    [("Debit card", s["debit_total_ticket_real"]), ("UPI P2M", s["p2m_ticket_real"]),
                     ("Credit card", s["credit_total_ticket_real"])], "₹ per transaction"),
        data=f"{RBI_CARDS}; {NPCI_P2M}; MoSPI CPI")


def h4(s):
    def share(kind):
        tot = [a + b + c for a, b, c in zip(s[f"p2m_{kind}"], s[f"debit_total_{kind}"], s[f"credit_total_{kind}"])]
        return [p / t for p, t in zip(s[f"p2m_{kind}"], tot)]
    vol, val = share("n"), share("v")
    gap = sub(diff(logodds(vol)), diff(logodds(val)))
    result = drift_hypothesis(
        "H4", "Merchant payments", "UPI's share gain is larger in volume than in value",
        "In the merchant basket (UPI P2M + debit + credit), UPI P2M's share rises faster in "
        "transaction count than in rupee value.",
        "UPI is used for small tickets, so it gains transactions faster than rupees.",
        "Mean of [Δ12 log-odds(volume share) − Δ12 log-odds(value share)]; log-odds because shares "
        "are bounded near 100%", ">0", gap, "log",
        chart("lines", "UPI P2M share of the merchant basket", s["months"],
              [("Volume share", [100 * v for v in vol]), ("Value share", [100 * v for v in val])], "%"),
        f"{RBI_CARDS}; {NPCI_P2M}")
    result["note"] = (f"In percentage points the ranking flips: volume share {100 * vol[0]:.1f}% → "
                      f"{100 * vol[-1]:.1f}%, value share {100 * val[0]:.1f}% → {100 * val[-1]:.1f}%. "
                      "The metric is fixed in advance as log-odds.")
    return result


def h5(s):
    gpos, goth = diff(ln(s["debit_pos_n"])), diff(ln(s["debit_other_n"]))
    return drift_hypothesis(
        "H5", "Merchant payments", "Debit decline is concentrated in-store",
        "Debit-card purchases at PoS terminals fall faster than online ('Others') debit purchases.",
        "UPI QR competes most directly at the shop counter.",
        "Mean of [Δ12 ln(debit PoS) − Δ12 ln(debit online)]", "<0", sub(gpos, goth), "log",
        growth_chart("Debit volume by channel, 12-month change", s["months"],
                     [("Debit PoS", gpos), ("Debit online/other", goth)]),
        RBI_CARDS)


def comovement(s, rail, hid, title, framework):
    y = diff(ln(s[f"{rail}_n"]))
    xp, xc = diff(ln(s["p2m_n"])), diff(ln(s["cpi"]))
    rows = [(a, b, c) for a, b, c in zip(y, xp, xc) if None not in (a, b, c)]
    x = [[1.0, b, c] for _, b, c in rows]
    fit = ols([a for a, _, _ in rows], x)
    se = hac_se(fit, x)
    b, sb = fit["beta"][1], se[1]
    lo, hi = b - Z95 * sb, b + Z95 * sb
    name = "debit" if rail.startswith("debit") else "credit"
    return hypothesis(
        id=hid, group="Merchant payments", title=title,
        statement=f"Months with faster UPI P2M growth show slower {name}-card growth, beyond common trends.",
        framework=framework,
        test=f"Δ12 ln({name}) = a + b·Δ12 ln(P2M) + c·Δ12 ln(CPI) + e; Newey-West (12-lag) SE; "
             f"n = {len(rows)}. Supported if b < 0 with the 95% interval below zero. "
             "b is an association elasticity, not a causal or cross-price elasticity.",
        estimate=f"b = {b:+.3f} [95% CI {lo:+.3f}, {hi:+.3f}]",
        consistency=f"p = {p_two_sided(b, sb):.3f}", p_value=p_two_sided(b, sb),
        ci=[lo, hi], point=b, verdict=verdict(lo, hi, "<0"),
        chart=growth_chart("12-month volume change", s["months"],
                           [("UPI P2M", xp), (name.capitalize() + " card", y)]),
        data=f"{RBI_CARDS}; {NPCI_P2M}; MoSPI CPI")


def h6(s):
    return comovement(s, "debit_total", "H6", "Debit co-moves negatively with UPI growth",
                      "Month-to-month substitution: if UPI growth pulls payments from debit, the "
                      "two growth rates should move in opposite directions.")


def h6b(s):
    return comovement(s, "credit_total", "H6b", "Credit co-moves negatively with UPI growth",
                      "Same test for the weaker substitute; a smaller or zero association would fit "
                      "the closeness-of-substitutes ranking.")


def s1(s):
    gap = sub(diff(ln(s["debit_total_n"])), diff(ln(s["credit_total_n"])))
    share = [d / (d + c) for d, c in zip(s["debit_total_n"], s["credit_total_n"])]
    result = drift_hypothesis(
        "S1", "Closeness of substitutes", "Debit loses ground faster than credit",
        "Debit-card purchase volume grows more slowly than credit-card purchase volume.",
        "Both card types share terminals, networks, reporting and macro conditions, but debit is the "
        "closer substitute for account-funded UPI.",
        "Mean of [Δ12 ln(debit) − Δ12 ln(credit)]", "<0", gap, "log",
        chart("lines", "Debit share of card purchases (volume)", s["months"],
              [("Debit share of cards", [100 * v for v in share])], "%"),
        RBI_CARDS)
    result["note"] = (f"Debit share of card purchases {100 * share[0]:.1f}% → {100 * share[-1]:.1f}%. "
                      "Rival explanations (bank push on credit, rewards, income growth) also predict this.")
    return result


def s2(s):
    gap = sub(diff(ln(s["debit_total_v"])), diff(ln(s["debit_total_n"])))
    return drift_hypothesis(
        "S2", "Closeness of substitutes", "Debit value falls more slowly than debit volume",
        "The debit payments being lost are mainly small ones, so value declines less than count.",
        "Selection by ticket size: UPI replaces small debit payments first.",
        "Mean of [Δ12 ln(debit value) − Δ12 ln(debit volume)], nominal", ">0", gap, "log",
        growth_chart("Debit card, 12-month change", s["months"],
                     [("Value", diff(ln(s["debit_total_v"]))), ("Volume", diff(ln(s["debit_total_n"])))]),
        RBI_CARDS)


def s3(s):
    g = diff(ln(s["credit_total_ticket_real"]))
    return drift_hypothesis(
        "S3", "Closeness of substitutes", "Credit cards move into everyday purchases",
        "The average real credit-card payment falls.",
        "Credit retains a role in smaller purchases (rewards, credit period), consistent with "
        "Adhikari (2026)'s reading of falling credit tickets.",
        "Mean of Δ12 ln(real credit ticket)", "<0", g, "growth",
        growth_chart("Real credit-card ticket, 12-month change", s["months"], [("Credit ticket", g)]),
        f"{RBI_CARDS}; MoSPI CPI")


def u1(s):
    share = [m / (m + p) for m, p in zip(s["p2m_n"], s["p2p_n"])]
    return drift_hypothesis(
        "U1", "Within UPI", "UPI shifts from transfers to merchant payments",
        "P2M's share of UPI transactions rises.",
        "Cross-side network effect: as merchant acceptance (QR) spreads, UPI use shifts toward purchases.",
        "Mean of Δ12 log-odds(P2M share of UPI volume)", ">0", diff(logodds(share)), "log",
        chart("lines", "P2M share of UPI transactions", s["months"],
              [("P2M share", [100 * v for v in share])], "%"),
        NPCI_P2M)


def h7(s):
    months = [m for m in s["long_months"] if m >= BREAK_SEARCH_START]
    offset = s["long_months"].index(months[0])
    y = ln(s["imps_n"][offset:])
    n = len(y)
    dummies = month_dummies(months)
    base_x = [[1.0, float(t)] + dummies[t] for t in range(n)]
    base = ols(y, base_x)
    best = None
    for b in range(int(0.15 * n), int(0.85 * n)):
        x = [row + [1.0 if t >= b else 0.0, float(t - b) if t >= b else 0.0]
             for t, row in enumerate(base_x)]
        fit = ols(y, x)
        f = ((base["ssr"] - fit["ssr"]) / 2) / (fit["ssr"] / (n - fit["k"]))
        if best is None or f > best[0]:
            best = (f, b, fit["beta"][1], fit["beta"][-1])
    f, b, slope, change = best
    before, after = 12 * slope, 12 * (slope + change)
    rolling = [None if i < 11 else sum(s["imps_n"][i - 11:i + 1]) / 1000 for i in range(len(s["imps_n"]))]
    parts = ["Supported" if f > SUP_F_CRITICAL_5PCT else "Not supported",
             "Supported" if after < 0 else "Not supported"]
    return hypothesis(
        id="H7", group="Transfers", title="IMPS turns from share loss to absolute contraction",
        statement="IMPS volume shows a structural break after which it declines, not just loses share to UPI.",
        framework="As UPI's network covers more transfer use cases, the older rail stops growing.",
        test=f"Quandt–Andrews sup-F for a break in level and slope of ln(IMPS volume), "
             f"{months[0]}–{months[-1]}, month dummies, 15% trimming; 5% critical value ≈ "
             f"{SUP_F_CRITICAL_5PCT}. Residual autocorrelation inflates F, so treat as indicative.",
        estimate=f"sup-F = {f:.1f} at {months[b]}; trend {pct(before)} a year before, {pct(after)} after",
        consistency=f"Rolling 12-month IMPS volume peaked at {max(r for r in rolling if r):.2f} bn "
                    f"and is {rolling[-1]:.2f} bn in the latest 12 months",
        p_value=None, ci=None, point=f, verdict=combine(parts),
        components=[{"name": "Significant break", "verdict": parts[0]},
                    {"name": "Declining after the break", "verdict": parts[1]}],
        chart=chart("lines", "IMPS volume, rolling 12 months", s["long_months"],
                    [("IMPS (bn)", rolling)], "billion transactions", marker=months[b]),
        data="NPCI IMPS product statistics")


def h10(s):
    months, y = s["months"], s["debit_total_n"]
    train_n = months.index(FORECAST_TRAIN_END) + 1
    test = list(range(train_n, len(months)))
    t = [float(i) for i in range(len(months))]
    lin = ols(y[:train_n], [[1.0, t[i]] for i in range(train_n)])
    dummies = month_dummies(months)
    lx = [[1.0, t[i]] + dummies[i] for i in range(len(months))]
    loglin = ols(ln(y[:train_n]), lx[:train_n])
    sigma2 = loglin["ssr"] / (loglin["n"] - loglin["k"])
    forecasts = {
        "Linear level trend": [lin["beta"][0] + lin["beta"][1] * t[i] for i in test],
        "Log-linear + month dummies": [math.exp(sum(b * c for b, c in zip(loglin["beta"], lx[i])) + sigma2 / 2)
                                       for i in test],
        "Seasonal naive": [y[i - 12] for i in test],
        "Seasonal naive × last-year growth": [y[i - 12] * y[i - 12] / y[i - 24] for i in test],
    }
    mape = {k: 100 * sum(abs(y[i] - f) / y[i] for i, f in zip(test, v)) / len(test) for k, v in forecasts.items()}
    best = min(mape, key=mape.get)
    ok = mape["Log-linear + month dummies"] < min(mape["Linear level trend"], mape["Seasonal naive"])
    series = [("Actual", y)] + [(k, [None] * train_n + v) for k, v in forecasts.items()]
    return hypothesis(
        id="H10", group="Forecasting", title="A log-scale seasonal model forecasts debit volume best",
        statement="A log-linear model with month dummies forecasts debit-card volume better than a "
                  "linear level trend and a seasonal-naive benchmark.",
        framework="Demand forecasting (course module): the decline is proportional and seasonal, not a "
                  "fixed number of transactions per month.",
        test=f"Train {months[0]}–{FORECAST_TRAIN_END}, forecast the next {len(test)} months; compare mean "
             "absolute percentage error (MAPE). Damped Holt-Winters (MAPE ≈ 4%) is in planning/pilot_tests.py.",
        estimate="; ".join(f"{k} {v:.1f}%" for k, v in mape.items()),
        consistency=f"Lowest MAPE: {best}", p_value=None, ci=None,
        point=mape["Log-linear + month dummies"], verdict="Supported" if ok else "Not supported",
        chart=chart("lines", "Debit-card volume: actual vs out-of-sample forecasts", months, series,
                    "million transactions", marker=months[train_n]),
        data=RBI_CARDS)


def pending(hid, group, title, statement, framework, test, data):
    return hypothesis(id=hid, group=group, title=title, statement=statement, framework=framework,
                      test=test, estimate="—", consistency="—", p_value=None, ci=None, point=None,
                      verdict="Pending data", data=data)


def h8(s):
    title = "Debit-card payments fall while cards remain in force"
    cards = s["debit_cards_outstanding_lakh"]
    if cards is None:
        return pending("H8", "Network and acceptance", title,
                       "Domestic debit-card payments per card in force fall while the number of cards rises.",
                       "Declining payment intensity alongside a stable or growing stock of card instruments.",
                       "Mean 12-month log growth in cards outstanding and in payments per card.",
                       "RBI PSI Part III: debit cards outstanding")
    per_card = [payments * 10 / stock for payments, stock in zip(s["debit_total_n"], cards)]
    card_growth, use_growth = diff(ln(cards)), diff(ln(per_card))
    card_test, use_test = drift(card_growth), drift(use_growth)
    card_verdict = verdict(card_test["lo"], card_test["hi"], ">0")
    use_verdict = verdict(use_test["lo"], use_test["hi"], "<0")
    march_2022 = s["months"].index("2022-03")
    return hypothesis(
        id="H8", group="Network and acceptance", title=title,
        statement="Domestic debit-card payments per card in force fall while the number of cards rises.",
        framework="A decline in payment intensity alongside growth in cards in force is consistent "
                  "with substitution in use, but does not identify the payment method chosen instead.",
        test="Mean 12-month log change in cards outstanding (> 0) and domestic debit-card "
             "payments per card (< 0), each with Newey-West (12-lag) standard errors over "
             "39 matched months, Jan 2023–Mar 2026. Both 95% intervals must be on the "
             "predicted side of zero for full support.",
        estimate=(f"Cards {pct(card_test['est'])}/year [95% CI {pct(card_test['lo'])}, "
                  f"{pct(card_test['hi'])}]; payments/card {pct(use_test['est'])}/year "
                  f"[95% CI {pct(use_test['lo'])}, {pct(use_test['hi'])}]."),
        consistency=(f"Cards rose in {card_test['pos']} of {card_test['n']} matched months; "
                     f"payments/card fell in {use_test['neg']} of {use_test['n']}."),
        p_value=None, ci=None, point=None,
        verdict=combine([card_verdict, use_verdict]),
        components=[{"name": "Cards in force rise", "verdict": card_verdict},
                    {"name": "Payments per card fall", "verdict": use_verdict}],
        chart=growth_chart("Debit cards in force and payments per card, 12-month change",
                           s["months"], [("Cards outstanding", card_growth),
                                         ("Payments per card", use_growth)]),
        data="RBI Payment System Indicators: Part I 4.2 domestic debit-card payments; "
             "Part III 1.2 debit cards outstanding (lakh), own-month release pages.",
        note=(f"Same-month comparison: Mar 2022 {s['debit_total_n'][march_2022] * 10:,.2f} "
              f"lakh domestic debit-card payments / {cards[march_2022]:,.2f} lakh cards "
              f"= {per_card[march_2022]:.3f} payments/card; Mar 2026 "
              f"{s['debit_total_n'][-1] * 10:,.2f} lakh payments / {cards[-1]:,.2f} lakh "
              f"cards = {per_card[-1]:.3f} payments/card. Cards in force are not distinct "
              "people; aggregate data cannot show who kept a card or whether they switched to UPI. "
              "ATM cash withdrawals are excluded."))


def h9(s):
    title = "UPI QR codes grow faster than PoS terminals"
    pos, qr = s["pos_terminals_lakh"], s["upi_qr_codes_lakh"]
    if pos is None or qr is None:
        return pending("H9", "Network and acceptance", title,
                       "UPI QR codes grow faster than card PoS terminals, while card purchases "
                       "per PoS terminal fall.",
                       "Wider QR deployment alongside lower card use per terminal is consistent "
                       "with a change in merchant payment infrastructure.",
                       "Mean 12-month log growth gap in UPI QR versus PoS terminal counts, "
                       "and in card PoS payments per terminal.",
                       "RBI PSI Part III: UPI QR codes and PoS terminals; Part I: card PoS purchases")
    card_pos_per_terminal = [10 * (debit + credit) / terminals for debit, credit, terminals
                             in zip(s["debit_pos_n"], s["credit_pos_n"], pos)]
    qr_growth, pos_growth = diff(ln(qr)), diff(ln(pos))
    gap_growth = sub(qr_growth, pos_growth)
    use_growth = diff(ln(card_pos_per_terminal))
    gap_test, use_test = drift(gap_growth), drift(use_growth)
    gap_verdict = verdict(gap_test["lo"], gap_test["hi"], ">0")
    use_verdict = verdict(use_test["lo"], use_test["hi"], "<0")
    return hypothesis(
        id="H9", group="Network and acceptance", title=title,
        statement="UPI QR codes grow faster than card PoS terminals, while card purchases "
                  "per PoS terminal fall.",
        framework="Wider QR deployment alongside lower card use per terminal is consistent "
                  "with a change in merchant payment infrastructure; these aggregate series "
                  "do not establish a network effect.",
        test="Two parts: mean [Δ12 ln(UPI QR codes) − Δ12 ln(PoS terminals)] > 0, and "
             "mean Δ12 ln((debit + credit PoS purchase volume) / PoS terminals) < 0. "
             "Newey-West (12-lag) standard errors over 39 matched months, Jan 2023–Mar 2026. "
             "Both 95% intervals must be on the predicted side of zero for full support.",
        estimate=(f"UPI QR minus PoS terminal growth {drift_line(gap_test)[0]}; "
                  f"card PoS payments/terminal {drift_line(use_test, 'growth')[0]}"),
        consistency=(f"UPI QR grew faster in {gap_test['pos']} of {gap_test['n']} matched months; "
                     f"card payments/terminal fell in {use_test['neg']} of {use_test['n']}."),
        p_value=None, ci=None, point=None,
        verdict=combine([gap_verdict, use_verdict]),
        components=[{"name": "UPI QR grows faster than PoS terminals", "verdict": gap_verdict},
                    {"name": "Card PoS payments per terminal fall", "verdict": use_verdict}],
        chart=growth_chart("QR and PoS infrastructure and card use, 12-month change",
                           s["months"], [("UPI QR codes", qr_growth),
                                         ("PoS terminals", pos_growth),
                                         ("Card PoS payments per terminal", use_growth)]),
        data="RBI Payment System Indicators: Part I 4.1/4.2 domestic credit- and debit-card "
             "PoS purchase volume; Part III UPI QR codes and PoS terminals (lakh), "
             "own-month release pages.",
        note="QR-code and terminal counts measure deployed acceptance instruments, not unique "
             "active merchants. Card PoS purchases per terminal is an aggregate ratio; these "
             "series cannot show individual merchant switching or establish causality.")


REGISTER = [h1, h2, h3, h4, h5, h6, h6b, s1, s2, s3, u1, h7, h10, h8, h9]


def main() -> None:
    series = load()
    results = [build(series) for build in REGISTER]
    OUTPUT.mkdir(exist_ok=True)
    with (OUTPUT / "hypotheses.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "group", "title", "verdict", "estimate", "consistency", "test", "data"])
        for r in results:
            writer.writerow([r["id"], r["group"], r["title"], r["verdict"], r["estimate"],
                             r["consistency"], r["test"], r["data"]])
    payload = {"dataThrough": END_MONTH, "merchantStart": MERCHANT_START, "hypotheses": results}
    DOCS_DATA.write_text(
        "// Generated by analysis/hypothesis_tests.py. Do not edit.\n"
        f"window.HYPOTHESES_DATA = Object.freeze({json.dumps(payload, separators=(',', ':'), ensure_ascii=False)});\n",
        encoding="utf-8")
    for r in results:
        print(f"{r['id']:4s} {r['verdict']:20s} {r['title']}\n     {r['estimate']}")


if __name__ == "__main__":
    main()
