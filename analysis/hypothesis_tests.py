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
            "neg": sum(v < 0 for v in clean), "pos": sum(v > 0 for v in clean), "values": values}


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


def label_month(month: str) -> str:
    names = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{names[int(month[5:7]) - 1]} {month[:4]}"


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
    sign = "below" if d["est"] < 0 else "above"
    return text, f"{consistent} of the {d['n']} year-on-year changes were {sign} zero"


def yoy_example(series, unit="log"):
    """Explain which year-on-year comparisons the average covers, with first, latest and extreme values."""
    months = month_range(MERCHANT_START, END_MONTH)
    points = [(m, v) for m, v in zip(months, series) if v is not None]
    show = (lambda v: pct(v)) if unit == "growth" else (lambda v: f"{v:+.3f}")

    def vs(m):
        return f"{label_month(m)} vs {label_month(f'{int(m[:4]) - 1}{m[4:]}')}"

    (m0, v0), (m1, v1) = points[0], points[1]
    last_m, last_v = points[-1]
    low = min(points, key=lambda p: p[1])
    high = max(points, key=lambda p: p[1])
    return (f"The result is the average of {len(points)} year-on-year changes, one per month from "
            f"{label_month(m0)} to {label_month(last_m)} — not a single year. Each compares a month with "
            f"the same month a year earlier: {vs(m0)} = {show(v0)}, {vs(m1)} = {show(v1)}, …, "
            f"{vs(last_m)} = {show(last_v)}. Range: {show(low[1])} ({label_month(low[0])}) to "
            f"{show(high[1])} ({label_month(high[0])}). Each bar or point in the chart is one of these changes."
            + (f" The average is taken in log points ({sum(v for _, v in points) / len(points):+.3f}) and "
               f"then converted to a percentage, so it differs slightly from averaging the percentages."
               if unit == "growth" else ""))


def hypothesis(**fields):
    fields.setdefault("components", [])
    fields.setdefault("chart", None)
    fields.setdefault("formulas", [])
    return fields


DRIFT_TEST = ("Mean 12-month log change, Newey-West (12-lag) standard errors, "
              "39 matched months Jan 2023–Mar 2026")

# LaTeX building blocks for the formulas shown on docs/hypotheses.html (rendered with KaTeX).
D12 = r"\Delta_{12}"
REAL_TICKET = (r"\tau^{X}_t = \frac{V^{X}_t}{N^{X}_t}\cdot"
               r"\frac{\mathrm{CPI}_{\text{Mar 2026}}}{\mathrm{CPI}_t}")
CARD_SYMBOLS = (r"N^{D}_t,\ N^{C}_t,\ N^{P}_t = \text{debit, credit, UPI P2M volume};\quad "
                r"V^{X}_t = \text{value of rail } X")


def drift_formulas(series_tex, direction, where="", tag=""):
    """Tested series, hypotheses and decision rule for one drift test, as (label, LaTeX) pairs."""
    g = "g_t" if not tag else "g^{" + tag + "}_t"
    mu = r"\mu" if not tag else r"\mu_{" + tag + "}"
    mu_hat = r"\hat\mu" if not tag else r"\hat\mu_{" + tag + "}"
    se = r"1.96\,\widehat{\mathrm{SE}}_{\mathrm{NW}}(" + mu_hat + ")"
    rule = mu_hat + " + " + se + " < 0" if direction == "<0" else mu_hat + " - " + se + " > 0"
    rows = [("Tested series", g + " = " + series_tex)]
    if where:
        rows.append(("where", where))
    rows += [("Hypothesis", "H_0:\\ " + mu + r" = 0 \qquad H_1:\ " + mu
              + (" < 0" if direction == "<0" else " > 0") + r",\qquad " + mu + r" = \mathbb{E}[" + g + "]"),
             ("Supported if", rule)]
    return rows


MULTI_COVERAGE = ("Each figure is an average of 39 year-on-year changes, one per month from Jan 2023 "
                  "(vs Jan 2022) to Mar 2026 (vs Mar 2025) — not a single year. The steps below show "
                  "the first, latest and range of each.")


def check_text(lo, hi, direction, fmt="{:+.3f}"):
    """Plain-language reason for a verdict from a 95% interval and the predicted sign."""
    side = "below" if direction == "<0" else "above"
    result = verdict(lo, hi, direction)
    bounds = f"[{fmt.format(lo)}, {fmt.format(hi)}]"
    if result == "Supported":
        return f"The whole interval {bounds} is {side} 0, as predicted."
    if result == "Contradicted":
        return f"The whole interval {bounds} is on the opposite side of 0 to the prediction."
    return f"The interval {bounds} includes 0, so the data cannot rule out no change."


def drift_step(question, d, direction, sym=r"\hat\mu", unit="log"):
    """One step of a verdict walk-through: the question, the numbers, the check and its verdict."""
    calc = (sym + f" = {d['est']:+.3f}" + r",\quad \widehat{\mathrm{SE}}_{\mathrm{NW}} = "
            + f"{d['se']:.3f}" + r",\quad \text{95\% interval} = " + f"{d['est']:+.3f}"
            + r" \pm 1.96 \times " + f"{d['se']:.3f} = [{d['lo']:+.3f},\\ {d['hi']:+.3f}]")
    if unit == "growth":
        calc += r"\;\Rightarrow\; e^{" + f"{d['est']:+.3f}" + r"} - 1 = " + pct(d["est"]).replace("%", r"\%") + r"\text{ a year}"
    return {"question": question, "calc": calc, "check": check_text(d["lo"], d["hi"], direction),
            "verdict": verdict(d["lo"], d["hi"], direction), "example": yoy_example(d["values"], unit)}


def derivation(steps, final):
    if len(steps) == 1:
        rule = "One test, so the final verdict is that test's verdict."
    else:
        rule = ("Every step must be Supported for the hypothesis to be Supported. If only some are, "
                "it is Partially supported; if none are, Not supported (Contradicted when every step "
                "points the opposite way).")
    return {"steps": steps, "rule": rule, "verdict": final}


def drift_hypothesis(hid, group, title, statement, framework, metric, direction, series,
                     unit, chart_spec, data, formula="", where="", question=""):
    d = drift(series)
    estimate, consistency = drift_line(d, unit)
    final = verdict(d["lo"], d["hi"], direction)
    question = question or f"Is the average 12-month change {'below' if direction == '<0' else 'above'} zero?"
    return hypothesis(
        id=hid, group=group, title=title, statement=statement, framework=framework,
        test=f"{metric}. {DRIFT_TEST}. Supported if the 95% interval is {'below' if direction == '<0' else 'above'} zero.",
        estimate=estimate, consistency=consistency, p_value=p_two_sided(d["est"], d["se"]),
        ci=[d["lo"], d["hi"]], point=d["est"], verdict=final,
        chart=chart_spec, data=data, coverage=yoy_example(series, unit),
        formulas=drift_formulas(formula, direction, where) if formula else [],
        derivation=derivation([{k: v for k, v in drift_step(question, d, direction, unit=unit).items()
                                if k != "example"}], final))


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
        RBI_CARDS, D12 + r"\ln N^{D}_t", r"N^{D}_t = \text{domestic debit-card purchase volume in month } t",
        question="Is debit-card purchase volume lower than a year earlier, on average?")


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
        data=f"{RBI_CARDS}; {NPCI_P2M}",
        coverage=MULTI_COVERAGE,
        derivation=derivation([
            drift_step("Is credit-card purchase volume growing year on year?", d_credit, ">0",
                       r"\hat\mu_{C}", "growth"),
            drift_step("Is UPI P2M growing faster than credit cards (so credit loses share)?", d_gap, ">0",
                       r"\hat\mu_{P-C}")], combine(parts)),
        formulas=drift_formulas(D12 + r"\ln N^{C}_t", ">0", CARD_SYMBOLS, tag="C")
        + drift_formulas(D12 + r"\ln N^{P}_t - " + D12 + r"\ln N^{C}_t", ">0", tag="P-C"))


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
        data=f"{RBI_CARDS}; {NPCI_P2M}; MoSPI CPI",
        coverage=MULTI_COVERAGE,
        derivation=derivation([
            drift_step("Is the average real debit-card payment getting larger?", dd, ">0", r"\hat\mu_{D}", "growth"),
            drift_step("Is the average real UPI P2M payment getting smaller?", dp, "<0", r"\hat\mu_{P}", "growth")],
            combine(parts)),
        formulas=[("Real ticket", REAL_TICKET), ("where", CARD_SYMBOLS)]
        + drift_formulas(D12 + r"\ln \tau^{D}_t", ">0", tag="D")
        + drift_formulas(D12 + r"\ln \tau^{P}_t", "<0", tag="P"))


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
        f"{RBI_CARDS}; {NPCI_P2M}",
        D12 + r"\,\mathrm{logit}\, s^{N}_t - " + D12 + r"\,\mathrm{logit}\, s^{V}_t",
        r"s^{N}_t = \frac{N^{P}_t}{N^{P}_t + N^{D}_t + N^{C}_t},\quad "
        r"s^{V}_t = \frac{V^{P}_t}{V^{P}_t + V^{D}_t + V^{C}_t},\quad "
        r"\mathrm{logit}\, s = \ln\frac{s}{1-s}",
        question="Does UPI P2M's volume share (in log-odds) rise faster than its value share?")
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
        RBI_CARDS, D12 + r"\ln N^{D,\mathrm{PoS}}_t - " + D12 + r"\ln N^{D,\mathrm{online}}_t",
        r"N^{D,\mathrm{PoS}}_t,\ N^{D,\mathrm{online}}_t = \text{debit purchases at PoS terminals and online (RBI 'Others')}",
        question="Does in-store (PoS) debit volume grow more slowly than online debit volume?")


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
    sym = "D" if name == "debit" else "C"
    formulas = [
        ("Regression", D12 + r"\ln N^{" + sym + r"}_t = a + b\," + D12 + r"\ln N^{P}_t + c\,"
         + D12 + r"\ln \mathrm{CPI}_t + e_t"),
        ("where", r"N^{" + sym + r"}_t = \text{" + name + r"-card purchase volume},\quad "
         r"N^{P}_t = \text{UPI P2M volume}"),
        ("Hypothesis", r"H_0:\ b = 0 \qquad H_1:\ b < 0"),
        ("Supported if", r"\hat b + 1.96\,\widehat{\mathrm{SE}}_{\mathrm{NW}}(\hat b) < 0"),
    ]
    step = {"question": f"In months when UPI P2M grows faster, does {name}-card volume grow more slowly?",
            "calc": (r"\hat b = " + f"{b:+.3f}" + r",\quad \widehat{\mathrm{SE}}_{\mathrm{NW}} = " + f"{sb:.3f}"
                     + r",\quad \text{95\% interval} = " + f"{b:+.3f}" + r" \pm 1.96 \times " + f"{sb:.3f}"
                     + f" = [{lo:+.3f},\\ {hi:+.3f}]"),
            "check": check_text(lo, hi, "<0"), "verdict": verdict(lo, hi, "<0")}
    first = next(i for i, r in enumerate(zip(y, xp, xc)) if None not in r)
    coverage = (f"b is estimated from {len(rows)} months, {label_month(s['months'][first])} to "
                f"{label_month(s['months'][-1])}. Each month pairs that month's year-on-year change in "
                f"{name}-card volume with the year-on-year change in UPI P2M volume (e.g. "
                f"{label_month(s['months'][first])} vs {label_month(s['months'][first - 12])}: {name} "
                f"{pct(y[first])}, P2M {pct(xp[first])}). b is the average link across all of them, "
                "not the result for one year.")
    return hypothesis(formulas=formulas, derivation=derivation([step], verdict(lo, hi, "<0")),
                      coverage=coverage,
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
        RBI_CARDS, D12 + r"\ln N^{D}_t - " + D12 + r"\ln N^{C}_t",
        r"N^{D}_t,\ N^{C}_t = \text{debit- and credit-card purchase volume}",
        question="Does debit volume grow more slowly than credit volume?")
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
        RBI_CARDS, D12 + r"\ln V^{D}_t - " + D12 + r"\ln N^{D}_t \;=\; " + D12 + r"\ln \frac{V^{D}_t}{N^{D}_t}",
        r"V^{D}_t,\ N^{D}_t = \text{debit-card purchase value (nominal) and volume}",
        question="Does debit value grow faster (fall more slowly) than debit volume, i.e. does the average debit payment rise?")


def s3(s):
    g = diff(ln(s["credit_total_ticket_real"]))
    return drift_hypothesis(
        "S3", "Closeness of substitutes", "Credit cards move into everyday purchases",
        "The average real credit-card payment falls.",
        "Credit retains a role in smaller purchases (rewards, credit period), consistent with "
        "Adhikari (2026)'s reading of falling credit tickets.",
        "Mean of Δ12 ln(real credit ticket)", "<0", g, "growth",
        growth_chart("Real credit-card ticket, 12-month change", s["months"], [("Credit ticket", g)]),
        f"{RBI_CARDS}; MoSPI CPI", D12 + r"\ln \tau^{C}_t", REAL_TICKET.replace("{X}", "{C}"),
        question="Is the average real credit-card payment smaller than a year earlier?")


def u1(s):
    share = [m / (m + p) for m, p in zip(s["p2m_n"], s["p2p_n"])]
    return drift_hypothesis(
        "U1", "Within UPI", "UPI shifts from transfers to merchant payments",
        "P2M's share of UPI transactions rises.",
        "Cross-side network effect: as merchant acceptance (QR) spreads, UPI use shifts toward purchases.",
        "Mean of Δ12 log-odds(P2M share of UPI volume)", ">0", diff(logodds(share)), "log",
        chart("lines", "P2M share of UPI transactions", s["months"],
              [("P2M share", [100 * v for v in share])], "%"),
        NPCI_P2M, D12 + r"\,\mathrm{logit}\, \pi_t",
        r"\pi_t = \frac{N^{\mathrm{P2M}}_t}{N^{\mathrm{P2M}}_t + N^{\mathrm{P2P}}_t},\quad "
        r"\mathrm{logit}\, \pi = \ln\frac{\pi}{1-\pi}",
        question="Is P2M's share of UPI transactions (in log-odds) higher than a year earlier?")


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
        data="NPCI IMPS product statistics",
        coverage=(f"The break search uses every monthly IMPS volume from {label_month(months[0])} to "
                  f"{label_month(months[-1])} ({n} months). The trends are average yearly rates over the "
                  f"whole period before {label_month(months[b])} and the whole period after it, not one year."),
        derivation=derivation([
            {"question": "Is there a clear break in the level or trend of IMPS volume?",
             "calc": r"\sup F = " + f"{f:.1f}" + r"\ \text{at } \hat\tau = \text{" + label_month(months[b])
                     + r"},\qquad \text{5\% critical value} = " + f"{SUP_F_CRITICAL_5PCT:.2f}",
             "check": (f"{f:.1f} is {'above' if f > SUP_F_CRITICAL_5PCT else 'not above'} "
                       f"{SUP_F_CRITICAL_5PCT:.2f}, so {'a break is detected' if f > SUP_F_CRITICAL_5PCT else 'no break is detected'}."),
             "verdict": parts[0]},
            {"question": "After the break, is IMPS volume falling (not just growing more slowly)?",
             "calc": (r"\text{before: } 12\hat\beta = " + f"{before:+.3f}" + r"\ (" + pct(before).replace("%", r"\%")
                      + r"),\qquad \text{after: } 12(\hat\beta + \hat\theta) = " + f"{after:+.3f}" + r"\ ("
                      + pct(after).replace("%", r"\%") + r")\ \text{a year}"),
             "check": (f"The post-break trend {pct(after)} a year is "
                       f"{'below 0: IMPS is contracting' if after < 0 else 'not below 0'}."),
             "verdict": parts[1]}], combine(parts)),
        formulas=[
            ("Model", r"\ln I_t = \alpha + \beta t + \sum_{m=2}^{12} \gamma_m M_{mt} + \delta\,\mathbf{1}[t \ge \tau]"
             r" + \theta\,(t-\tau)\,\mathbf{1}[t \ge \tau] + e_t"),
            ("where", r"I_t = \text{IMPS volume},\quad M_{mt} = \text{calendar-month dummies},\quad "
             r"\tau = \text{candidate break month}"),
            ("Break statistic", r"F(\tau) = \frac{\big(\mathrm{SSR}_0 - \mathrm{SSR}_1(\tau)\big)/2}"
             r"{\mathrm{SSR}_1(\tau)/(n-k)},\qquad \sup F = \max_{0.15n \,\le\, \tau \,\le\, 0.85n} F(\tau)"),
            ("Hypothesis", r"H_0:\ \delta = \theta = 0 \qquad H_1:\ \text{a break exists and } 12(\beta + \theta) < 0"),
            ("Supported if", r"\sup F > " + f"{SUP_F_CRITICAL_5PCT:.2f}" + r"\quad\text{and}\quad 12(\hat\beta + \hat\theta) < 0"),
        ])


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
        data=RBI_CARDS,
        coverage=(f"Models are fitted on {label_month(months[0])}–{label_month(FORECAST_TRAIN_END)} only. "
                  f"Each MAPE is the average % error over the {len(test)} held-out months "
                  f"{label_month(months[train_n])}–{label_month(months[-1])}; e.g. log-linear in "
                  f"{label_month(months[train_n])}: forecast {forecasts['Log-linear + month dummies'][0]:,.0f} mn "
                  f"vs actual {y[train_n]:,.0f} mn."),
        derivation=derivation([
            {"question": f"Fit each model on {months[0]} to {FORECAST_TRAIN_END}, forecast the next "
                         f"{len(test)} months, and measure the average % error (MAPE).",
             "calc": r",\quad ".join(r"\text{" + k.replace("×", r"$\times$") + r"} = " + f"{v:.1f}" + r"\%"
                                     for k, v in mape.items()),
             "check": (f"Log-linear ({mape['Log-linear + month dummies']:.1f}%) "
                       f"{'beats' if ok else 'does not beat'} both the linear trend "
                       f"({mape['Linear level trend']:.1f}%) and seasonal naive "
                       f"({mape['Seasonal naive']:.1f}%). "
                       + (f"The best overall is {best} ({mape[best]:.1f}%), which is reported but is "
                          "not part of the pre-set rule." if best != "Log-linear + month dummies" else "")),
             "verdict": "Supported" if ok else "Not supported"}], "Supported" if ok else "Not supported"),
        formulas=[
            ("Linear trend", r"\hat N^{D}_t = \hat\alpha + \hat\beta t"),
            ("Log-linear", r"\ln N^{D}_t = \alpha + \beta t + \sum_{m=2}^{12} \gamma_m M_{mt} + e_t,\qquad "
             r"\hat N^{D}_t = \exp\!\big(\widehat{\ln N^{D}_t} + \hat\sigma^2/2\big)"),
            ("Seasonal naive", r"\hat N^{D}_t = N^{D}_{t-12}"),
            ("Naive × growth", r"\hat N^{D}_t = N^{D}_{t-12}\cdot \frac{N^{D}_{t-12}}{N^{D}_{t-24}}"),
            ("Accuracy", r"\mathrm{MAPE} = \frac{100}{h}\sum_{t \in \text{holdout}} "
             r"\frac{\lvert N^{D}_t - \hat N^{D}_t\rvert}{N^{D}_t},\qquad h = " + str(len(test))),
            ("Supported if", r"\mathrm{MAPE}_{\text{log-linear}} < \min\big(\mathrm{MAPE}_{\text{linear}},\ "
             r"\mathrm{MAPE}_{\text{seasonal naive}}\big)"),
        ])


def pending(hid, group, title, statement, framework, test, data, formulas=()):
    return hypothesis(id=hid, group=group, title=title, statement=statement, framework=framework,
                      test=test, estimate="—", consistency="—", p_value=None, ci=None, point=None,
                      verdict="Pending data", data=data, formulas=list(formulas))


H8_FORMULAS = ([("Use per card", r"u_t = \frac{N^{D}_t}{K_t},\qquad K_t = \text{debit cards in force}")]
               + drift_formulas(D12 + r"\ln K_t", ">0", tag="K")
               + drift_formulas(D12 + r"\ln u_t", "<0", tag="u"))
H9_FORMULAS = ([("Use per terminal", r"w_t = \frac{N^{D,\mathrm{PoS}}_t + N^{C,\mathrm{PoS}}_t}{T_t},\qquad "
                 r"Q_t = \text{UPI QR codes},\ T_t = \text{PoS terminals}")]
               + drift_formulas(D12 + r"\ln Q_t - " + D12 + r"\ln T_t", ">0", tag="Q-T")
               + drift_formulas(D12 + r"\ln w_t", "<0", tag="w"))


def h8(s):
    title = "Debit-card payments fall while cards remain in force"
    cards = s["debit_cards_outstanding_lakh"]
    if cards is None:
        return pending("H8", "Network and acceptance", title,
                       "Domestic debit-card payments per card in force fall while the number of cards rises.",
                       "Declining payment intensity alongside a stable or growing stock of card instruments.",
                       "Mean 12-month log growth in cards outstanding and in payments per card.",
                       "RBI PSI Part III: debit cards outstanding", H8_FORMULAS)
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
        verdict=combine([card_verdict, use_verdict]), formulas=H8_FORMULAS,
        coverage=MULTI_COVERAGE,
        derivation=derivation([
            drift_step("Is the number of debit cards in force rising?", card_test, ">0", r"\hat\mu_{K}", "growth"),
            drift_step("Are payments per card falling?", use_test, "<0", r"\hat\mu_{u}", "growth")],
            combine([card_verdict, use_verdict])),
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
                       "RBI PSI Part III: UPI QR codes and PoS terminals; Part I: card PoS purchases",
                       H9_FORMULAS)
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
        verdict=combine([gap_verdict, use_verdict]), formulas=H9_FORMULAS,
        coverage=MULTI_COVERAGE,
        derivation=derivation([
            drift_step("Are UPI QR codes growing faster than card PoS terminals?", gap_test, ">0", r"\hat\mu_{Q-T}"),
            drift_step("Are card payments per PoS terminal falling?", use_test, "<0", r"\hat\mu_{w}", "growth")],
            combine([gap_verdict, use_verdict])),
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
