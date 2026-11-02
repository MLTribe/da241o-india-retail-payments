#!/usr/bin/env python3
"""Final analysis: the Assignment 2 design plus the review additions.

Follows planning/PLAN_Final_Term_Paper.md (v2). Every Assignment 2 rejection rule is kept as the
pre-stated test; the v2 tests that let each hypothesis fail are reported beside it.

  * Classification: drawdown D_i of the 12-month average volume from its own peak, share change
    of 12-month averages against a reference window, thresholds 5/10/20%, real value, PSI-period
    window, RBI daily data (UPI, IMPS) and a secondary comparison group.
  * Share-change decomposition into own growth and comparison-group growth.
  * Trend tests: mean 12-month log change with Newey-West (12) errors (primary), the A2 OLS trend
    with month, Diwali and COVID dummies, a segmented version (slope change Jan 2020) and the
    card regressions with and without cards outstanding / PoS terminals. ADF, KPSS, Ljung-Box.
  * H1-H3 verdicts, a least-squares break search with LR-inversion confidence sets for H2, the
    pre/post-COVID table, managerial magnitudes and sensitivity checks.

Data window: Nov 2017 to the last month every monthly series covers (END_MONTH); the RBI daily
file extends UPI and IMPS further. Writes analysis/output/tp_*.csv, tp_findings.md and
docs/final-data.js (shown on docs/final.html). Run: python3 analysis/final_analysis.py
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hypothesis_tests import Z95, hac_cov, ols  # noqa: E402
from long_series import SPLICE_START, ZERO_MDR, break_fit, chi2_sf, load, read_all  # noqa: E402
from payment_mix_model import END_MONTH, INPUT, OUTPUT, REPO, label_month, month_range, shift_month  # noqa: E402

DOCS_DATA = REPO / "docs" / "final-data.js"

START = "2017-11"
END = END_MONTH
P2M_START = "2020-04"
PSI_START = SPLICE_START
LONG_REF = ("2017-11", "2018-10")
P2M_REF = ("2020-07", "2021-06")
PSI_REF = ("2021-06", "2022-05")
DAILY_START = "2020-11"
PERIODS = [("Pre-COVID", "2017-11", "2020-02"), ("COVID", "2020-03", "2021-06"), ("Post-COVID", "2021-07", END)]
COVID_MONTHS = month_range("2020-03", "2020-06") + month_range("2021-04", "2021-06")
# Month of Diwali (Lakshmi Puja); festival spending moves between October and November.
DIWALI = {"2017-10", "2018-11", "2019-10", "2020-11", "2021-11", "2022-10", "2023-11", "2024-10", "2025-10"}
SAME_ACCOUNT = "2018-08"
CARD_DEFINITION = "2019-11"
CARD_LAYOUT = "2022-03"
RUPAY_CREDIT_ON_UPI = "2022-09"
THRESHOLDS = (0.05, 0.10, 0.20)
PRIMARY_TH = 0.10
TRIM = 0.15
LR95 = 7.35  # Hansen (2000) LR-inversion critical value, 95%
ADF_CRIT_5 = -3.45  # constant and trend, T about 100 (MacKinnon)
KPSS_CRIT_5 = 0.146  # trend stationarity
HAC = 12

RAILS = {"upi": "UPI total", "imps": "IMPS", "p2m": "UPI P2M", "debit": "Debit cards", "credit": "Credit cards"}
GROUPS = {
    "ui": ("UPI + IMPS", ("upi", "imps")),
    "pc": ("P2M + debit + credit", ("p2m", "debit", "credit")),
    "uc": ("UPI total + debit + credit", ("upi", "debit", "credit")),
    "cc": ("Debit + credit", ("debit", "credit")),
}
CONTRACTION, SHARE_LOSS, GROWTH = "Absolute contraction", "Relative share loss", "Continued growth"


# ---------- helpers ----------

def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def logit(p):
    return math.log(p / (1 - p))


def norm_sf(z):
    return 0.5 * math.erfc(z / math.sqrt(2))


def pct(log_change):
    return 100 * (math.exp(log_change) - 1)


def r(v, d=4):
    return None if v is None else round(v, d)


def write_csv(name, rows):
    path = OUTPUT / name
    fields = []
    for row in rows:
        fields += [k for k in row if k not in fields]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: (round(v, 6) if isinstance(v, float) else v) for k, v in row.items()})


def read_rows(filename):
    rows = []
    for path in sorted(INPUT.glob(f"*/{filename}")):
        with path.open(newline="", encoding="utf-8-sig") as stream:
            rows += list(csv.DictReader(stream))
    return rows


# ---------- panel ----------

def build_panel():
    s = load()
    months = s["months"]
    col = lambda key: {m: v for m, v in zip(months, s[key]) if v is not None}  # noqa: E731
    P = {k: col(f"{k}_n") for k in ("upi", "imps", "p2m", "debit", "credit")}
    P = {f"{k}_n": v for k, v in P.items()}
    for rail in ("debit", "credit"):
        P[f"{rail}_v"] = col(f"{rail}_v")
    upi, imps = read_all("npci_upi_product_statistics.csv"), read_all("npci_imps_product_statistics.csv")
    split = read_all("npci_upi_p2p_p2m_transactions.csv")
    P["upi_v"] = {m: float(x["value_crore"]) for m, x in upi.items()}
    P["imps_v"] = {m: float(x["value_crore"]) for m, x in imps.items()}
    P["p2m_v"] = {m: float(x["p2m_value_crore"]) for m, x in split.items()}
    cpi = {m: float(x["cpi_combined_index_linked_2024"]) for m, x in read_all("mospi_cpi_combined_monthly.csv").items()}
    base = cpi[END]
    for k in RAILS:
        P[f"{k}_rv"] = {m: v * base / cpi[m] for m, v in P[f"{k}_v"].items() if m in cpi}
        P[f"{k}_t"] = {m: P[f"{k}_rv"][m] * 10 / P[f"{k}_n"][m] for m in P[f"{k}_rv"] if P[f"{k}_n"].get(m)}
        P[f"{k}_tn"] = {m: P[f"{k}_v"][m] * 10 / P[f"{k}_n"][m] for m in P[f"{k}_v"] if P[f"{k}_n"].get(m)}

    bank = read_all("rbi_bankwise_card_totals.csv")
    infra = read_all("rbi_psi_infrastructure.csv")
    bank_level = defaultdict(Counter)
    for row in read_rows("rbi_bankwise_cards.csv"):
        for key, value in row.items():
            if key not in ("month", "bank", "bank_name_as_published", "bank_group") and value:
                bank_level[row["month"][:7]][key] += float(value)
    stock = lambda psi_field, bank_field: {  # noqa: E731
        **{m: float(x[bank_field]) / 1e5 for m, x in bank.items() if x.get(bank_field)},
        **{m: float(x[psi_field]) for m, x in infra.items() if x.get(psi_field)}}
    P["credit_cards"] = stock("credit_cards_outstanding", "credit_cards")
    P["debit_cards"] = stock("debit_cards_outstanding", "debit_cards")
    P["pos"] = stock("pos_terminals", "pos_terminals")
    P["debit_atm_n"] = {**{m: float(x["debit_atm_n"]) / 1e6 for m, x in bank.items() if x.get("debit_atm_n")},
                        **{m: c["debit_atm_cash_n"] / 1e6 for m, c in bank_level.items()}}

    daily = defaultdict(dict)
    with (OUTPUT / "monthly_five_systems.csv").open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            daily[row["system"]][row["period_start"][:7]] = float(row["volume_mn"])
    P["upi_n_daily"], P["imps_n_daily"] = daily["UPI"], daily["IMPS"]

    for g, (_, members) in GROUPS.items():
        for measure in ("n", "v"):
            series = [P[f"{k}_{measure}"] for k in members]
            common = set.intersection(*(set(x) for x in series))
            for k, x in zip(members, series):
                P[f"sh_{k}_{g}_{measure}"] = {m: x[m] / sum(y[m] for y in series) for m in common}
    common = set(P["upi_n_daily"]) & set(P["imps_n_daily"])
    P["sh_imps_ui_daily"] = {m: P["imps_n_daily"][m] / (P["imps_n_daily"][m] + P["upi_n_daily"][m]) for m in common}
    return P, s["splice"], split, bank, bank_level


# ---------- classification ----------

def roll12(series, start, end):
    out = {}
    for m in month_range(shift_month(start, 11), end):
        window = month_range(shift_month(m, -11), m)
        if all(w in series for w in window):
            out[m] = sum(series[w] for w in window) / 12
    return out


def label_for(drawdown, share_change, th):
    if drawdown <= -th:
        return CONTRACTION
    return SHARE_LOSS if share_change < 0 else GROWTH


def classify(level, share, window, ref, end=END):
    rolled = roll12(level, window[0], end)
    peak = max(rolled, key=rolled.get)
    drawdown = rolled[end] / rolled[peak] - 1
    running, path, crossing = -math.inf, {}, {th: None for th in THRESHOLDS}
    for m in sorted(rolled):
        running = max(running, rolled[m])
        path[m] = rolled[m] / running - 1
        for th in THRESHOLDS:
            # Start of the spell below the threshold that is still running at the end of the window.
            if path[m] <= -th:
                crossing[th] = crossing[th] or m
            else:
                crossing[th] = None
    share_end = mean([share.get(m) for m in month_range(shift_month(end, -11), end)])
    share_ref = mean([share.get(m) for m in month_range(*ref)])
    change = 100 * (share_end - share_ref)
    return {"peak": peak, "D": drawdown, "shareRef": 100 * share_ref, "shareEnd": 100 * share_end,
            "dS": change, "labels": {th: label_for(drawdown, change, th) for th in THRESHOLDS},
            "crossing": crossing, "path": path, "rollEnd": rolled[end], "rollPeak": rolled[peak]}


def classification(P):
    daily_end = max(m for m in P["imps_n_daily"] if m in P["upi_n_daily"])
    specs = {
        "IMPS": [
            ("Primary", "volume", "imps_n", "sh_imps_ui_n", (START, END), LONG_REF, END, "ui"),
            ("Real value", "real value", "imps_rv", "sh_imps_ui_v", (START, END), LONG_REF, END, "ui"),
            ("PSI-period window", "volume", "imps_n", "sh_imps_ui_n", (PSI_START, END), PSI_REF, END, "ui"),
            ("RBI daily data", "volume", "imps_n_daily", "sh_imps_ui_daily", (DAILY_START, daily_end),
             (DAILY_START, shift_month(DAILY_START, 11)), daily_end, "ui"),
        ],
    }
    for rail, name in (("debit", "Debit cards"), ("credit", "Credit cards")):
        specs[name] = [
            ("Primary", "volume", f"{rail}_n", f"sh_{rail}_pc_n", (START, END), P2M_REF, END, "pc"),
            ("Real value", "real value", f"{rail}_rv", f"sh_{rail}_pc_v", (START, END), P2M_REF, END, "pc"),
            ("PSI-period window", "volume", f"{rail}_n", f"sh_{rail}_pc_n", (PSI_START, END), PSI_REF, END, "pc"),
            ("Secondary group", "volume", f"{rail}_n", f"sh_{rail}_uc_n", (START, END), LONG_REF, END, "uc"),
            ("Within cards", "volume", f"{rail}_n", f"sh_{rail}_cc_n", (START, END), LONG_REF, END, "cc"),
        ]
    rows, summary, paths, grid = [], [], {}, []
    for method, variants in specs.items():
        results = {}
        for variant, measure, level, share, window, ref, end, group in variants:
            c = classify(P[level], P[share], window, ref, end)
            results[variant] = c
            rows.append({"method": method, "variant": variant, "measure": measure, "group": GROUPS[group][0],
                         "window": f"{label_month(window[0])} – {label_month(end)}",
                         "reference": f"{label_month(ref[0])} – {label_month(ref[1])}",
                         "peak12mEnding": c["peak"], "drawdownPct": 100 * c["D"],
                         "shareRefPct": c["shareRef"], "shareEndPct": c["shareEnd"], "shareChangePp": c["dS"],
                         **{f"label{int(th * 100)}": c["labels"][th] for th in THRESHOLDS},
                         **{f"crossed{int(th * 100)}": c["crossing"][th] or "" for th in THRESHOLDS}})
            if variant == "Primary":
                paths[method] = c["path"]
        primary = results["Primary"]["labels"][PRIMARY_TH]
        checks = [("Threshold 5%", results["Primary"]["labels"][0.05]),
                  ("Threshold 20%", results["Primary"]["labels"][0.20]),
                  ("Real value", results["Real value"]["labels"][PRIMARY_TH]),
                  ("PSI-period window", results["PSI-period window"]["labels"][PRIMARY_TH])]
        checks.append(("RBI daily data", results["RBI daily data"]["labels"][PRIMARY_TH]) if method == "IMPS"
                      else ("Secondary group", results["Secondary group"]["labels"][PRIMARY_TH]))
        agree = sum(lab == primary for _, lab in checks)
        psi_agrees = dict(checks)["PSI-period window"] == primary
        summary.append({"method": method, "label": primary, "drawdownPct": 100 * results["Primary"]["D"],
                        "peak": results["Primary"]["peak"], "shareChangePp": results["Primary"]["dS"],
                        "shareRefPct": results["Primary"]["shareRef"], "shareEndPct": results["Primary"]["shareEnd"],
                        "group": rows[[i for i, x in enumerate(rows) if x["method"] == method][0]]["group"],
                        "agree": agree, "variants": len(checks), "robust": agree >= len(checks) - 1 and psi_agrees,
                        "checks": [{"variant": v, "label": lab, "agrees": lab == primary} for v, lab in checks],
                        "crossing": {str(int(th * 100)): results["Primary"]["crossing"][th] for th in THRESHOLDS}})
        for variant in ("Primary", "Real value", "PSI-period window"):
            for th in THRESHOLDS:
                grid.append({"method": method, "variant": "Volume, bridged" if variant == "Primary" else
                             "Real value" if variant == "Real value" else "Volume, PSI period",
                             "threshold": int(th * 100), "label": results[variant]["labels"][th]})
    return rows, summary, paths, grid


# ---------- share decomposition ----------

def decomposition(P):
    rows = []
    specs = [("IMPS", "imps", "ui", LONG_REF), ("Debit cards", "debit", "pc", P2M_REF),
             ("Credit cards", "credit", "pc", P2M_REF), ("Debit cards", "debit", "uc", LONG_REF),
             ("Credit cards", "credit", "uc", LONG_REF), ("Debit cards", "debit", "cc", LONG_REF),
             ("Credit cards", "credit", "cc", LONG_REF)]
    last = month_range(shift_month(END, -11), END)
    for method, rail, g, ref in specs:
        members = GROUPS[g][1]
        ref_months = month_range(*ref)
        own0, own1 = mean([P[f"{rail}_n"][m] for m in ref_months]), mean([P[f"{rail}_n"][m] for m in last])
        grp0 = mean([sum(P[f"{k}_n"][m] for k in members) for m in ref_months])
        grp1 = mean([sum(P[f"{k}_n"][m] for k in members) for m in last])
        sh0, sh1 = own0 / grp0, own1 / grp1
        rows.append({"method": method, "group": GROUPS[g][0],
                     "from": f"{label_month(ref[0])} – {label_month(ref[1])}",
                     "to": f"{label_month(last[0])} – {label_month(last[-1])}",
                     "ownGrowthPct": 100 * (own1 / own0 - 1), "groupGrowthPct": 100 * (grp1 / grp0 - 1),
                     "shareFromPct": 100 * sh0, "shareToPct": 100 * sh1, "shareChangePp": 100 * (sh1 - sh0),
                     "logShareChange": math.log(sh1 / sh0), "logOwn": math.log(own1 / own0),
                     "logGroup": math.log(grp1 / grp0),
                     "driver": ("own decline" if own1 < own0 else "group grew faster") if sh1 < sh0 else "gained share"})
    return rows


# ---------- trend tests ----------

def drift12(series, window, skip=lambda m: False):
    vals = []
    for m in month_range(*window):
        b = shift_month(m, -12)
        if b >= window[0] and m in series and b in series and not skip(m):
            vals.append(series[m] - series[b])
    if len(vals) < 6:
        return None
    x = [[1.0] for _ in vals]
    fit = ols(vals, x)
    se = math.sqrt(max(hac_cov(fit, x, HAC)[0][0], 0.0))
    est = fit["beta"][0]
    z = est / se if se > 0 else 0.0
    return {"est": est, "se": se, "n": len(vals), "pPos": norm_sf(z), "pNeg": norm_sf(-z),
            "lo": est - Z95 * se, "hi": est + Z95 * se}


def ljung_box(resid, lags=12):
    n = len(resid)
    mu = sum(resid) / n
    e = [v - mu for v in resid]
    denom = sum(v * v for v in e)
    q = 0.0
    for k in range(1, lags + 1):
        rho = sum(e[t] * e[t - k] for t in range(k, n)) / denom
        q += rho * rho / (n - k)
    q *= n * (n + 2)
    return q, chi2_sf(q, lags)


def design(months, kind, segmented=False, controls=None, extra=None, covid=True, card_definition=True):
    """Month dummies, Diwali, one dummy per COVID month and the A2 level shifts for the rail type."""
    t0 = month_range("2017-01", "2020-01").__len__() - 1
    index = {m: i for i, m in enumerate(month_range("2017-01", "2027-12"))}
    names = ["const", "trend"] + [f"m{c}" for c in range(2, 13)] + ["diwali"]
    if covid:
        names += [f"cv_{m}" for m in COVID_MONTHS if m in months]
    levels = []
    if kind == "upi" and months[0] < SAME_ACCOUNT <= months[-1]:
        levels.append(("sa", SAME_ACCOUNT))
    if kind == "card":
        dates = (CARD_DEFINITION, PSI_START, CARD_LAYOUT) if card_definition else (PSI_START, CARD_LAYOUT)
        levels += [(f"lv_{d}", d) for d in dates if months[0] < d <= months[-1]]
    names += [n for n, _ in levels]
    if segmented:
        names.append("slope_after_jan2020")
    names += [f"ctl_{c}" for c in (controls or {})]
    names += [n for n, _ in (extra or [])]
    rows = []
    for m in months:
        t = (index[m] - t0) / 12
        row = [1.0, t] + [1.0 if int(m[5:7]) == c else 0.0 for c in range(2, 13)] + [1.0 if m in DIWALI else 0.0]
        if covid:
            row += [1.0 if m == c else 0.0 for c in COVID_MONTHS if c in months]
        row += [1.0 if m >= d else 0.0 for _, d in levels]
        if segmented:
            row.append(max(t, 0.0))
        row += [controls[c][m] for c in (controls or {})]
        row += [f(m) for _, f in (extra or [])]
        rows.append(row)
    keep, seen = [], set()
    for j in range(len(names)):
        column = tuple(row[j] for row in rows)
        if (j < 2 or len(set(column)) > 1) and column not in seen:
            keep.append(j)
            seen.add(column)
    return [names[j] for j in keep], [[row[j] for j in keep] for row in rows]


def trend_regression(series, window, kind, segmented=False, controls=None):
    months = [m for m in month_range(*window) if m in series and all(m in c for c in (controls or {}).values())]
    names, x = design(months, kind, segmented, controls)
    y = [series[m] for m in months]
    fit = ols(y, x)
    cov = hac_cov(fit, x, HAC)
    se = [math.sqrt(max(cov[i][i], 0.0)) for i in range(len(names))]
    out = {"n": len(y), "k": len(names), "trend": fit["beta"][1], "trendSe": se[1]}
    out["pPos"] = norm_sf(out["trend"] / out["trendSe"])
    out["pNeg"] = norm_sf(-out["trend"] / out["trendSe"])
    if segmented:
        i = names.index("slope_after_jan2020")
        post = fit["beta"][1] + fit["beta"][i]
        post_se = math.sqrt(max(cov[1][1] + cov[i][i] + 2 * cov[1][i], 0.0))
        out.update({"slopeChange": fit["beta"][i], "slopeChangeSe": se[i], "postTrend": post, "postTrendSe": post_se})
    for c in (controls or {}):
        i = names.index(f"ctl_{c}")
        out[f"ctl_{c}"], out[f"ctl_{c}_se"] = fit["beta"][i], se[i]
    out["lbQ"], out["lbP"] = ljung_box(fit["resid"])
    return out


def transformed(P):
    """Log or logit series used by the trend, break and diagnostic tests."""
    T = {}
    for k in RAILS:
        T[f"ln {RAILS[k]} volume"] = ({m: math.log(v) for m, v in P[f"{k}_n"].items() if v > 0}, k)
        T[f"ln {RAILS[k]} real value"] = ({m: math.log(v) for m, v in P[f"{k}_rv"].items() if v > 0}, k)
        T[f"ln {RAILS[k]} real ticket"] = ({m: math.log(v) for m, v in P[f"{k}_t"].items()}, k)
        T[f"ln {RAILS[k]} nominal ticket"] = ({m: math.log(v) for m, v in P[f"{k}_tn"].items()}, k)
    for k in ("imps", "debit", "credit"):
        T[f"ln {RAILS[k]} ticket ÷ UPI total ticket"] = (
            {m: math.log(P[f"{k}_tn"][m] / P["upi_tn"][m]) for m in P[f"{k}_tn"] if m in P["upi_tn"]}, k)
    for k in ("debit", "credit"):
        T[f"ln {RAILS[k]} ticket ÷ P2M ticket"] = (
            {m: math.log(P[f"{k}_tn"][m] / P["p2m_tn"][m]) for m in P[f"{k}_tn"] if m in P["p2m_tn"]}, k)
    for name, key, rail in (("logit credit share of card volume", "sh_credit_cc_n", "credit"),
                            ("logit P2M share of P2M + cards", "sh_p2m_pc_n", "p2m"),
                            ("logit UPI share of UPI + IMPS", "sh_upi_ui_n", "upi")):
        T[name] = ({m: logit(v) for m, v in P[key].items() if 0 < v < 1}, rail)
    T["ln debit ÷ credit volume"] = ({m: math.log(P["debit_n"][m] / P["credit_n"][m]) for m in P["debit_n"]
                                      if m in P["credit_n"]}, "debit")
    return T


def kind_of(rail):
    return "card" if rail in ("debit", "credit") else "upi" if rail == "upi" else "other"


def trend_tests(P, T):
    rows = []
    for name, (series, rail) in T.items():
        first = max(START, min(series))
        window = (first, END)
        kind = kind_of(rail)
        # UPI comparisons across the Aug 2018 exclusion are dropped from the drift.
        skip = (lambda m: shift_month(m, -12) < SAME_ACCOUNT <= m) if rail == "upi" or "UPI total" in name else (lambda m: False)
        d = drift12(series, window, skip)
        if d:
            rows.append({"series": name, "method": "Drift: mean 12-month log change, NW(12)", "window": window,
                         "est": d["est"], "se": d["se"], "pPos": d["pPos"], "pNeg": d["pNeg"], "n": d["n"],
                         "pctPerYear": pct(d["est"])})
            for label, a, b in PERIODS:
                lo = max(a, first)
                if lo < b:
                    dp = drift12(series, (lo, b), skip)
                    if dp:
                        rows.append({"series": name, "method": f"Drift, {label}", "window": (lo, b), "est": dp["est"],
                                     "se": dp["se"], "pPos": dp["pPos"], "pNeg": dp["pNeg"], "n": dp["n"],
                                     "pctPerYear": pct(dp["est"])})
        a2 = trend_regression(series, window, kind)
        rows.append({"series": name, "method": "A2 OLS trend (month, Diwali, COVID dummies; NW 12)", "window": window,
                     "est": a2["trend"], "se": a2["trendSe"], "pPos": a2["pPos"], "pNeg": a2["pNeg"], "n": a2["n"],
                     "pctPerYear": pct(a2["trend"]), "ljungBoxP": a2["lbP"]})
        if first < ZERO_MDR:
            sg = trend_regression(series, window, kind, segmented=True)
            rows.append({"series": name, "method": "Segmented OLS: trend before / after Jan 2020", "window": window,
                         "est": sg["trend"], "se": sg["trendSe"], "pPos": sg["pPos"], "pNeg": sg["pNeg"], "n": sg["n"],
                         "pctPerYear": pct(sg["trend"]), "postTrend": sg["postTrend"], "postTrendSe": sg["postTrendSe"],
                         "postPctPerYear": pct(sg["postTrend"]), "slopeChange": sg["slopeChange"],
                         "slopeChangeSe": sg["slopeChangeSe"], "ljungBoxP": sg["lbP"]})
        if kind == "card" and name.endswith("volume"):
            controls = {"ln cards outstanding": {m: math.log(v) for m, v in P[f"{rail}_cards"].items()},
                        "ln PoS terminals": {m: math.log(v) for m, v in P["pos"].items()}}
            wc = trend_regression(series, window, kind, controls=controls)
            rows.append({"series": name, "method": "A2 OLS trend with cards outstanding and PoS terminals",
                         "window": window, "est": wc["trend"], "se": wc["trendSe"], "pPos": wc["pPos"],
                         "pNeg": wc["pNeg"], "n": wc["n"], "pctPerYear": pct(wc["trend"]), "ljungBoxP": wc["lbP"],
                         "controlCards": wc["ctl_ln cards outstanding"], "controlPos": wc["ctl_ln PoS terminals"]})
    for row in rows:
        row["window"] = f"{label_month(row['window'][0])} – {label_month(row['window'][1])}"
    return rows


def find(rows, series, method):
    return next(x for x in rows if x["series"] == series and x["method"] == method)


# ---------- diagnostics ----------

def adf(series, months, lags=2):
    y = [series[m] for m in months]
    dy = [None] + [y[i] - y[i - 1] for i in range(1, len(y))]
    rows, target = [], []
    for i in range(lags + 1, len(y)):
        m = months[i]
        rows.append([1.0, i / 12, y[i - 1]] + [dy[i - j] for j in range(1, lags + 1)]
                    + [1.0 if int(m[5:7]) == c else 0.0 for c in range(2, 13)])
        target.append(dy[i])
    fit = ols(target, rows)
    n, k = fit["n"], fit["k"]
    s2 = fit["ssr"] / (n - k)
    return fit["beta"][2] / math.sqrt(s2 * fit["inv"][2][2])


def kpss(series, months, lags=HAC):
    y = [series[m] for m in months]
    x = [[1.0, i / 12] + [1.0 if int(m[5:7]) == c else 0.0 for c in range(2, 13)] for i, m in enumerate(months)]
    e = ols(y, x)["resid"]
    n = len(e)
    s, cum = 0.0, []
    for v in e:
        s += v
        cum.append(s)
    lr = sum(v * v for v in e) / n
    for k in range(1, lags + 1):
        lr += 2 * (1 - k / (lags + 1)) * sum(e[t] * e[t - k] for t in range(k, n)) / n
    return sum(c * c for c in cum) / (n * n * lr)


def diagnostics(T):
    rows = []
    for name, (series, _) in T.items():
        if "nominal ticket" in name or "real value" in name:
            continue
        months = [m for m in month_range(max(START, min(series)), END) if m in series]
        a, k = adf(series, months), kpss(series, months)
        rows.append({"series": name, "months": len(months), "adfStat": a, "adfUnitRootRejected5": a < ADF_CRIT_5,
                     "kpssStat": k, "kpssStationarityRejected5": k > KPSS_CRIT_5,
                     "reading": "trend-stationary" if a < ADF_CRIT_5 and k <= KPSS_CRIT_5 else
                     "unit root likely" if a >= ADF_CRIT_5 and k > KPSS_CRIT_5 else "inconclusive"})
    return rows


# ---------- break search (H2) ----------

def break_search(series, window, kind, card_definition_dummy=True):
    months = [m for m in month_range(*window) if m in series]
    y = [series[m] for m in months]
    def fit_ssr(tau, tau2=None):
        extra = []
        for tt in [t for t in (tau, tau2) if t]:
            extra += [(f"lv_{tt}", lambda m, tt=tt: 1.0 if m >= tt else 0.0),
                      (f"sl_{tt}", lambda m, tt=tt: max(0.0, (months.index(m) - months.index(tt)) / 12))]
        names, x = design(months, kind, extra=extra, card_definition=card_definition_dummy)
        try:
            fit = ols(y, x)
        except ValueError:
            # Not identified: the break coincides with fixed dummies (e.g. Apr 2021 = Jun 2021 splice + wave-2 months).
            return math.inf, len(names)
        return fit["ssr"], len(names)
    n = len(y)
    lo, hi = int(TRIM * n), n - int(TRIM * n)
    candidates = months[lo:hi]
    ssr0, k0 = fit_ssr(None)
    ssr = {tau: v for tau in candidates if (v := fit_ssr(tau)[0]) < math.inf}
    best = min(ssr, key=ssr.get)
    k1 = k0 + 2
    sup_f = max(((ssr0 - v) / 2) / (v / (n - k1)) for v in ssr.values())
    bic0 = n * math.log(ssr0 / n) + k0 * math.log(n)
    bic1 = n * math.log(ssr[best] / n) + (k1 + 1) * math.log(n)
    conf = [tau for tau, v in ssr.items() if n * (v - ssr[best]) / ssr[best] <= LR95]
    # Sequential second break, at least TRIM*n months from the first.
    gap = int(TRIM * n)
    second = [tau for tau in ssr if abs(months.index(tau) - months.index(best)) >= gap]
    ssr2 = {tau: v for tau in second if (v := fit_ssr(*sorted((best, tau)))[0]) < math.inf}
    best2 = min(ssr2, key=ssr2.get) if ssr2 else None
    bic2 = n * math.log(ssr2[best2] / n) + (k1 + 3) * math.log(n) if best2 else math.inf
    n_breaks = min(((0, bic0), (1, bic1), (2, bic2)), key=lambda b: b[1])[0]
    return {"months": n, "break": best, "confFrom": min(conf), "confTo": max(conf), "confSize": len(conf),
            "supF": sup_f, "bic0": bic0, "bic1": bic1, "bic2": bic2, "bicBreaks": n_breaks,
            "secondBreak": best2 if n_breaks == 2 else None,
            "ssrJan2020": ssr.get("2020-01"), "ssrMar2020": ssr.get("2020-03"), "ssrBest": ssr[best],
            "lrJan2020": n * (ssr["2020-01"] - ssr[best]) / ssr[best] if "2020-01" in ssr else None,
            "lrMar2020": n * (ssr["2020-03"] - ssr[best]) / ssr[best] if "2020-03" in ssr else None}


def timing(b):
    covers_jan = b["confFrom"] <= "2020-01" <= b["confTo"]
    covers_mar = b["confFrom"] <= "2020-03" <= b["confTo"]
    if b["bicBreaks"] == 0:
        return "No break selected"
    if covers_jan and covers_mar:
        return "Indeterminate"
    if "2020-03" <= b["break"] <= "2020-12" and not covers_jan:
        return "COVID-timed"
    if "2019-10" <= b["break"] <= "2020-02" and not covers_mar:
        return "Zero-MDR-timed"
    return "Elsewhere"


def h2_tests(P, T):
    rows = []
    specs = [("IMPS", "ln IMPS volume", "other", True), ("Debit cards", "ln Debit cards volume", "card", True),
             ("Debit cards (no Nov 2019 dummy)", "ln Debit cards volume", "card", False),
             ("Credit cards", "ln Credit cards volume", "card", True),
             ("Credit cards (no Nov 2019 dummy)", "ln Credit cards volume", "card", False),
             ("Debit ÷ credit (nets common shocks)", "ln debit ÷ credit volume", "card", True)]
    for label, series_name, kind, dummy in specs:
        b = break_search(T[series_name][0], (START, END), kind, dummy)
        rows.append({"series": label, **b, "timing": timing(b)})
    s = load()
    ratio = [d / c if d and c else None for d, c in zip(s["debit_n"], s["credit_n"])]
    known = {}
    for tau in (ZERO_MDR, "2020-03"):
        known[tau] = break_fit(s["months"], ratio, tau, ("2017-04", "2023-03"), False, True, True)
    methods = [x for x in rows if x["series"] in ("IMPS", "Debit cards", "Credit cards") and x["bicBreaks"] > 0]
    mdr = sum(x["timing"] == "Zero-MDR-timed" for x in methods)
    covid = sum(x["timing"] == "COVID-timed" for x in methods)
    in_mar_apr = sum("2020-03" <= x["break"] <= "2020-04" for x in methods)
    in_mdr_window = sum("2019-10" <= x["break"] <= "2020-02" for x in methods)
    a2 = ("Rejected" if (methods and in_mdr_window > len(methods) / 2) or in_mar_apr == 0 else "Not rejected")
    indeterminate = sum(x["timing"] == "Indeterminate" for x in methods)
    ratio_jan = known[ZERO_MDR]
    ratio_sig = abs(ratio_jan["slope"] / ratio_jan["slopeSe"]) > Z95 if ratio_jan["slopeSe"] else False
    if indeterminate >= max(1, len(methods) / 2) or not methods:
        v2 = "Indeterminate"
    elif covid > len(methods) / 2 and not ratio_sig:
        v2 = "Supported"
    elif mdr > len(methods) / 2:
        v2 = "Not supported"
    else:
        v2 = "Partially supported"
    return rows, {"a2": a2, "v2": v2, "methodsWithBreak": len(methods), "covidTimed": covid, "mdrTimed": mdr,
                  "indeterminate": indeterminate, "inMarApr": in_mar_apr, "inMdrWindow": in_mdr_window,
                  "ratioJan": {k: ratio_jan[k] for k in ("slope", "slopeSe", "level", "levelSe", "p", "n")},
                  "ratioMar": {k: known["2020-03"][k] for k in ("slope", "slopeSe", "level", "levelSe", "p", "n")},
                  "ratioJanSignificant": ratio_sig}


# ---------- H1 and H3 ----------

def h1_tests(trend_rows):
    out, methods = [], []
    for rail, name in (("imps", "IMPS"), ("debit", "Debit cards")):
        rel = find(trend_rows, f"ln {name} ticket ÷ UPI total ticket", "A2 OLS trend (month, Diwali, COVID dummies; NW 12)")
        real = find(trend_rows, f"ln {name} real ticket", "A2 OLS trend (month, Diwali, COVID dummies; NW 12)")
        rel_d = find(trend_rows, f"ln {name} ticket ÷ UPI total ticket", "Drift: mean 12-month log change, NW(12)")
        own_d = find(trend_rows, f"ln {name} nominal ticket", "Drift: mean 12-month log change, NW(12)")
        upi_d = find(trend_rows, "ln UPI total nominal ticket", "Drift: mean 12-month log change, NW(12)")
        real_d = find(trend_rows, f"ln {name} real ticket", "Drift: mean 12-month log change, NW(12)")
        a2_pass = rel["pPos"] < 0.05 and real["pPos"] < 0.05
        v2_parts = [rel_d["pPos"] < 0.05, real_d["pPos"] < 0.05, own_d["est"] > 0]
        share_upi = (-upi_d["est"] / rel_d["est"]) if rel_d["est"] else None
        row = {"method": name, "a2RelTrend": rel["est"], "a2RelP": rel["pPos"], "a2RealTicketTrend": real["est"],
               "a2RealTicketP": real["pPos"], "a2Pass": a2_pass, "relDrift": rel_d["est"], "relDriftP": rel_d["pPos"],
               "ownNominalDrift": own_d["est"], "upiNominalDrift": upi_d["est"], "realTicketDrift": real_d["est"],
               "realTicketDriftP": real_d["pPos"], "shareFromUpiFall": share_upi, "v2Pass": all(v2_parts),
               "v2Partial": any(v2_parts)}
        if rail == "debit":
            p2m = find(trend_rows, "ln Debit cards ticket ÷ P2M ticket", "Drift: mean 12-month log change, NW(12)")
            row.update({"relP2mDrift": p2m["est"], "relP2mDriftP": p2m["pPos"]})
        out.append(row)
        methods.append(row)
    a2 = "Not rejected" if all(m["a2Pass"] for m in methods) else "Rejected"
    v2 = ("Supported" if all(m["v2Pass"] for m in methods) else
          "Partially supported" if any(m["v2Partial"] for m in methods) else "Not supported")
    return out, {"a2": a2, "v2": v2}


def h3_tests(P, trend_rows):
    lc = "logit credit share of card volume"
    a2_bridged = find(trend_rows, lc, "A2 OLS trend (month, Diwali, COVID dummies; NW 12)")
    psi_series = {m: logit(v) for m, v in P["sh_credit_cc_n"].items() if m >= PSI_START}
    a2_psi = trend_regression(psi_series, (PSI_START, END), "card")
    share = P["sh_p2m_pc_n"]
    sm = sorted(m for m in share if m <= END)
    gains = {m: 100 * (share[m] - share[shift_month(m, -1)]) for m in sm if shift_month(m, -1) in share}
    first12 = [gains[m] for m in month_range(shift_month(P2M_START, 1), shift_month(P2M_START, 12)) if m in gains]
    last12 = [gains[m] for m in month_range(shift_month(END, -11), END) if m in gains]
    leg2 = mean(last12) < mean(first12)
    a2 = "Not rejected" if a2_bridged["pPos"] < 0.05 and a2_psi["pPos"] < 0.05 and leg2 else "Rejected"

    pre = find(trend_rows, lc, "Drift, Pre-COVID")
    post = find(trend_rows, lc, "Drift, Post-COVID")
    diff = post["est"] - pre["est"]
    diff_se = math.sqrt(pre["se"] ** 2 + post["se"] ** 2)
    slope_break = diff / diff_se > Z95
    decomposition = []
    for label, a, b in PERIODS:
        a12 = month_range(a, shift_month(a, 11)) if shift_month(a, 11) <= b else month_range(a, b)
        b12 = month_range(shift_month(b, -11), b) if shift_month(b, -11) >= a else month_range(a, b)
        c0, c1 = mean([P["credit_n"][m] for m in a12]), mean([P["credit_n"][m] for m in b12])
        d0, d1 = mean([P["debit_n"][m] for m in a12]), mean([P["debit_n"][m] for m in b12])
        dl = math.log(c1 / c0) - math.log(d1 / d0)
        decomposition.append({"period": label, "from": f"{label_month(a12[0])} – {label_month(a12[-1])}",
                              "to": f"{label_month(b12[0])} – {label_month(b12[-1])}", "dLogit": dl,
                              "credit": math.log(c1 / c0), "debit": -math.log(d1 / d0),
                              "debitSharePct": 100 * (-math.log(d1 / d0)) / dl if dl else None})
    lshare = {m: logit(v) for m, v in share.items()}
    lg = {m: lshare[m] - lshare[shift_month(m, -1)] for m in lshare if shift_month(m, -1) in lshare}
    base_months = [m for m in month_range(*P2M_REF) if m in lg]
    last_months = [m for m in month_range(shift_month(END, -11), END) if m in lg]
    y = [lg[m] for m in base_months + last_months]
    x = [[1.0, 0.0] for _ in base_months] + [[1.0, 1.0] for _ in last_months]
    fit = ols(y, x)
    cov = hac_cov(fit, x, HAC)
    slow, slow_se = fit["beta"][1], math.sqrt(max(cov[1][1], 0.0))
    slowed = slow / slow_se < -Z95 if slow_se else False
    cards0 = mean([P["credit_cards"][m] for m in month_range(*PSI_REF)])
    cards1 = mean([P["credit_cards"][m] for m in month_range(shift_month(END, -11), END)])
    vol0 = mean([P["credit_n"][m] for m in month_range(*PSI_REF)])
    vol1 = mean([P["credit_n"][m] for m in month_range(shift_month(END, -11), END)])
    years = (len(month_range(PSI_REF[1], END)) - 1) / 12
    supply = {"cardsGrowthLog": math.log(cards1 / cards0), "volumeGrowthLog": math.log(vol1 / vol0),
              "intensityGrowthLog": math.log((vol1 / cards1) / (vol0 / cards0)), "years": years,
              "cardsSharePct": 100 * math.log(cards1 / cards0) / math.log(vol1 / vol0),
              "txnPerCard0": vol0 * 10 / cards0, "txnPerCard1": vol1 * 10 / cards1}
    post_dec = decomposition[-1]
    parts = [slope_break, (post_dec["debitSharePct"] or 0) > 50, slowed]
    v2 = "Supported" if all(parts) else "Partially supported" if any(parts) else "Not supported"
    return {"a2": a2, "v2": v2, "a2Bridged": {"trend": a2_bridged["est"], "p": a2_bridged["pPos"]},
            "a2Psi": {"trend": a2_psi["trend"], "p": a2_psi["pPos"], "n": a2_psi["n"]},
            "leg2": {"first12": mean(first12), "last12": mean(last12), "pass": leg2,
                     "note": "Mechanically expected: a share near its 100% ceiling must gain fewer points."},
            "slopeBreak": {"pre": pre["est"], "preSe": pre["se"], "post": post["est"], "postSe": post["se"],
                           "diff": diff, "diffSe": diff_se, "significant": slope_break},
            "decomposition": decomposition,
            "p2mLogOdds": {"base": fit["beta"][0], "change": slow, "changeSe": slow_se, "slowed": slowed,
                           "baseWindow": f"{label_month(P2M_REF[0])} – {label_month(P2M_REF[1])}",
                           "lastWindow": f"{label_month(last_months[0])} – {label_month(last_months[-1])}"},
            "supply": supply}


# ---------- pre/post COVID ----------

def covid_periods(P):
    rows = []
    for rail in ("upi", "imps", "p2m", "debit", "credit"):
        series = {m: math.log(v) for m, v in P[f"{rail}_n"].items() if v > 0}
        group = "ui" if rail in ("upi", "imps") else "pc"
        share = P[f"sh_{rail}_{group}_n"]
        cells = {}
        for label, a, b in PERIODS:
            lo = max(a, START)
            skip = (lambda m: shift_month(m, -12) < SAME_ACCOUNT <= m) if rail == "upi" else (lambda m: False)
            # Growth inside a period compares each month with the same month a year earlier; COVID months
            # are compared with pre-COVID bases on purpose.
            base_from = lo if label != "COVID" else shift_month(lo, -12)
            vals = []
            for m in month_range(lo, b):
                bm = shift_month(m, -12)
                if bm >= base_from and m in series and bm in series and not skip(m):
                    vals.append(series[m] - series[bm])
            if len(vals) < 3:
                cells[label] = None
                continue
            x = [[1.0] for _ in vals]
            fit = ols(vals, x)
            se = math.sqrt(max(hac_cov(fit, x, HAC)[0][0], 0.0))
            sm = [m for m in month_range(lo, b) if m in share]
            s0 = mean([share[m] for m in sm[:3]]) if sm else None
            s1 = mean([share[m] for m in sm[-3:]]) if sm else None
            est = fit["beta"][0]
            cls = (CONTRACTION if est + Z95 * se < 0 else
                   SHARE_LOSS if s0 is not None and s1 < s0 else "Co-expansion")
            cells[label] = {"growthPct": pct(est), "lo": pct(est - Z95 * se), "hi": pct(est + Z95 * se), "est": est,
                            "se": se, "n": len(vals), "shareFrom": None if s0 is None else 100 * s0,
                            "shareTo": None if s1 is None else 100 * s1, "class": cls}
        row = {"series": f"{RAILS[rail]} volume", "group": GROUPS[group][0], "periods": cells}
        pre, post = cells.get("Pre-COVID"), cells.get("Post-COVID")
        if pre and post:
            diff = post["est"] - pre["est"]
            se = math.sqrt(pre["se"] ** 2 + post["se"] ** 2)
            row.update({"diff": diff, "diffSe": se, "diffLo": diff - Z95 * se, "diffHi": diff + Z95 * se})
        rows.append(row)
    return rows


def flatten_covid(rows):
    flat = []
    for row in rows:
        for label, _, _ in PERIODS:
            c = row["periods"].get(label)
            if c:
                flat.append({"series": row["series"], "group": row["group"], "period": label,
                             **{k: v for k, v in c.items()}})
        if "diff" in row:
            flat.append({"series": row["series"], "group": row["group"], "period": "Post minus pre (log pts/yr)",
                         "est": row["diff"], "se": row["diffSe"], "lo": row["diffLo"], "hi": row["diffHi"]})
    return flat


# ---------- magnitudes, sensitivity, corrections ----------

def last12(series):
    return mean([series.get(m) for m in month_range(shift_month(END, -11), END)])


def magnitudes(P, summary):
    out = []
    add = lambda name, value, unit, note="": out.append({"name": name, "value": value, "unit": unit, "note": note})  # noqa: E731
    for rail in ("debit", "imps"):
        roll_n = roll12(P[f"{rail}_n"], START, END)
        roll_v = roll12(P[f"{rail}_rv"], START, END)
        pk, pv = max(roll_n, key=roll_n.get), max(roll_v, key=roll_v.get)
        add(f"{RAILS[rail]}: peak 12-month average volume", roll_n[pk], "million a month", f"12 months to {label_month(pk)}")
        add(f"{RAILS[rail]}: latest 12-month average volume", roll_n[END], "million a month", f"12 months to {label_month(END)}")
        add(f"{RAILS[rail]}: transactions forgone versus peak", roll_n[pk] - roll_n[END], "million a month", "")
        add(f"{RAILS[rail]}: real value forgone versus peak", roll_v[pv] - roll_v[END], "₹ crore a month (Mar 2026 prices)",
            f"value peak: 12 months to {label_month(pv)}")
    for rail in RAILS:
        add(f"{RAILS[rail]}: average real ticket, latest 12 months", last12(P[f"{rail}_t"]), "₹ (Mar 2026 prices)")
    for label, window in (("reference", P2M_REF), ("latest", (shift_month(END, -11), END))):
        ms = month_range(*window)
        card = sum(P["debit_v"][m] + P["credit_v"][m] for m in ms)
        total = card + sum(P["p2m_v"][m] for m in ms)
        add(f"Card share of merchant payment value (fee-bearing), {label}", 100 * card / total, "% of P2M + card value",
            f"{label_month(window[0])} – {label_month(window[1])}; cards carry MDR, UPI P2M has been at zero MDR")
    add("UPI P2M share of P2M + card volume, latest 12 months", 100 * last12(P["sh_p2m_pc_n"]), "%")
    for rail in ("debit", "credit"):
        f = lambda m: P[f"{rail}_n"][m] * 10 / P[f"{rail}_cards"][m]  # noqa: E731
        add(f"{RAILS[rail]}: payments per card per month, {label_month(PSI_REF[0])} – {label_month(PSI_REF[1])}",
            mean([f(m) for m in month_range(*PSI_REF)]), "payments per card per month")
        add(f"{RAILS[rail]}: payments per card per month, latest 12 months",
            mean([f(m) for m in month_range(shift_month(END, -11), END)]), "payments per card per month")
    g = lambda m: (P["debit_n"][m] + P["credit_n"][m]) * 1e6 / (P["pos"][m] * 1e5)  # noqa: E731
    add("Card payments per PoS terminal per month, first PSI year", mean([g(m) for m in month_range(*PSI_REF)]), "payments")
    add("Card payments per PoS terminal per month, latest 12 months",
        mean([g(m) for m in month_range(shift_month(END, -11), END)]), "payments",
        "Card payments include online use, so this overstates terminal use")
    atm = roll12(P["debit_atm_n"], START, END)
    pk = max(atm, key=atm.get)
    add("Debit-card ATM withdrawals: peak 12-month average", atm[pk], "million a month", f"12 months to {label_month(pk)}")
    add("Debit-card ATM withdrawals: latest 12-month average", atm[END], "million a month", "Cash context")
    return out


def classify_with(P, credit_n):
    return classify(credit_n, {m: credit_n[m] / (credit_n[m] + P["debit_n"][m] + P["p2m_n"][m])
                               for m in P["sh_credit_pc_n"]}, (START, END), P2M_REF)


def sensitivity(P, split):
    rows = []
    for cut in (0.0, 0.05, 0.10):
        adj = {m: v * (1 - cut) if m >= RUPAY_CREDIT_ON_UPI else v for m, v in P["credit_n"].items()}
        c = classify_with(P, adj)
        share = {m: logit(adj[m] / (adj[m] + P["debit_n"][m])) for m in adj if m in P["debit_n"]}
        d = drift12(share, ("2021-07", END))
        rows.append({"check": "RuPay credit on UPI: remove share of credit volume from Sep 2022",
                     "setting": f"{int(cut * 100)}%", "creditLabel": c["labels"][PRIMARY_TH], "creditDrawdownPct": 100 * c["D"],
                     "creditShareChangePp": c["dS"], "postCovidLogitCreditShareDrift": d["est"] if d else None})
    provenance = Counter()
    gaps = defaultdict(float)
    upi = read_all("npci_upi_product_statistics.csv")
    for m, x in split.items():
        src = "NPCI API JSON (archived, hash-checked)" if "ecosystem_api" in x["source_file"] else "NPCI web table (transcribed)"
        provenance[src] += 1
        inner = abs(float(x["p2p_volume_mn"]) + float(x["p2m_volume_mn"]) - float(x["total_volume_mn"]))
        outer = abs(float(x["total_volume_mn"]) / float(upi[m]["volume_mn"]) - 1) * 100 if m in upi else 0
        gaps[(src, "inner")] = max(gaps[(src, "inner")], inner)
        gaps[(src, "outer")] = max(gaps[(src, "outer")], outer)
    for src, n in provenance.items():
        rows.append({"check": "P2M provenance", "setting": src, "months": n,
                     "maxP2pPlusP2mGapMn": gaps[(src, "inner")], "maxGapVsProductStatsPct": gaps[(src, "outer")]})
    verified = [m for m, x in split.items() if "ecosystem_api" in x["source_file"]]
    rows.append({"check": "P2M verified months only", "setting": f"{label_month(min(verified))} – {label_month(max(verified))}",
                 "note": "The end-window share (latest 12 months) falls in transcribed months, so the P2M-group classification "
                         "cannot be run on archived API months alone; P2P + P2M totals match NPCI product statistics within the "
                         "gaps shown."})
    return rows


def bridge_checks(P, splice, bank, bank_level):
    psi = read_all("rbi_psi_card_ppi_own_month.csv")
    rows, summary = [], []
    for rail in ("debit", "credit"):
        for measure, psi_field, scale in (("volume", f"{rail}_total_volume_lakh", 0.1), ("value", f"{rail}_total_value_crore", 1.0)):
            gaps = []
            for m in sorted(psi):
                if m in bank:
                    b = float(bank[m][f"{rail}_payments_n"]) / 1e6 if measure == "volume" else float(bank[m][f"{rail}_payments_v_crore"])
                    source = "bank-wise totals (PoS column)"
                elif m in bank_level:
                    c = bank_level[m]
                    parts = ("pos", "online", "other")
                    b = (sum(c[f"{rail}_{p}_n"] for p in parts) / 1e6 if measure == "volume"
                         else sum(c[f"{rail}_{p}_v"] for p in parts) / 1e4)
                    source = "bank-level sums (PoS + online + other)"
                else:
                    continue
                official = float(psi[m][psi_field]) * scale
                gaps.append((m, source, 100 * (b / official - 1)))
                rows.append({"rail": rail, "measure": measure, "month": m, "source": source, "bankwise": b,
                             "psi": official, "gapPct": 100 * (b / official - 1)})
            after = [abs(g) for m, _, g in gaps if m >= CARD_LAYOUT]
            summary.append({"series": f"{rail.capitalize()} card {measure}", "months": len(gaps),
                            "from": label_month(gaps[0][0]), "to": label_month(gaps[-1][0]),
                            "maxAbsGapPct": max(abs(g) for _, _, g in gaps),
                            "maxAbsGapFromMar2022Pct": max(after) if after else None,
                            "exactFromMar2022": all(a < 0.01 for a in after)})
    return rows, summary, splice["factors"]


def corrections(bridge_summary, split):
    daily = defaultdict(set)
    gaps = defaultdict(float)
    with (OUTPUT / "daily_vs_official_monthly.csv").open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            daily[row["system"]].add(row["month"])
            if row["gap_pct"]:
                gaps[row["system"]] = max(gaps[row["system"]], abs(float(row["gap_pct"])))
    upi_months = sorted(daily["UPI"])
    return [
        {"item": "Card overlap with RBI PSI", "a2": "63 months (Jun 2021 – Aug 2026), largest gap 2.3%",
         "repo": f"{bridge_summary[0]['months']} months ({bridge_summary[0]['from']} – {bridge_summary[0]['to']}); largest gap "
                 f"{max(b['maxAbsGapPct'] for b in bridge_summary):.2f}%; from Mar 2022 the bank-level sums equal PSI exactly, "
                 "so the overlap confirms the transcription rather than the definitions"},
        {"item": "Card bridge method", "a2": "Direct join of bank-wise totals to PSI",
         "repo": "Bank-wise series scaled by k (geometric-mean PSI ÷ bank-wise ratio, Jun 2021 – Feb 2022) and spliced at Jun 2021"},
        {"item": "RBI Bulletin overlap", "a2": "Nov 2017 – Jun 2019, within 1.4%",
         "repo": "No Bulletin data or script in the repository; not reproduced"},
        {"item": "RBI daily file against monthly figures", "a2": "63 months from Jun 2020",
         "repo": f"{len(upi_months)} months ({label_month(upi_months[0])} – {label_month(upi_months[-1])}); largest UPI gap "
                 f"{gaps['UPI']:.2f}%, IMPS {gaps['IMPS']:.2f}%"},
        {"item": "UPI P2P/P2M months", "a2": "60 of 77 months, with gaps",
         "repo": f"{len(split)} continuous months ({label_month(min(split))} – {label_month(max(split))})"},
        {"item": "Data window", "a2": "Nov 2017 – Aug 2026 (106 months; 77 for P2M)",
         "repo": f"Nov 2017 – {label_month(END)} ({len(month_range(START, END))} months; "
                 f"{len(month_range(P2M_START, END))} for P2M); RBI daily data extend UPI and IMPS"},
    ]


# ---------- page data ----------

def page_series(P, paths):
    months = month_range(START, END)
    idx = lambda key, base: [r(100 * P[key][m] / P[key][base], 2) if m in P[key] else None for m in months]  # noqa: E731
    pm = month_range(P2M_START, END)
    idx2 = lambda key: [r(100 * P[key][m] / P[key][P2M_START], 2) for m in pm]  # noqa: E731
    sh = lambda key: [r(100 * P[key][m], 2) if m in P[key] else None for m in months]  # noqa: E731
    return {
        "months": months, "p2mMonths": pm,
        "index": [{"name": RAILS[k], "values": idx(f"{k}_n", START)} for k in ("upi", "imps", "debit", "credit")],
        "indexP2m": [{"name": RAILS[k], "values": idx2(f"{k}_n")} for k in ("p2m", "debit", "credit")],
        "shares": [{"name": "IMPS share of UPI + IMPS", "values": sh("sh_imps_ui_n")},
                   {"name": "Debit share of P2M + cards", "values": sh("sh_debit_pc_n")},
                   {"name": "Credit share of P2M + cards", "values": sh("sh_credit_pc_n")},
                   {"name": "Credit share of debit + credit", "values": sh("sh_credit_cc_n")}],
        "tickets": [{"name": RAILS[k], "values": [r(P[f"{k}_t"].get(m), 1) for m in months]} for k in RAILS],
        "drawdown": [{"name": k, "values": [r(100 * v, 2) if (v := p.get(m)) is not None else None for m in months]}
                     for k, p in paths.items()],
        "bands": [{"from": "2020-03", "to": "2020-06", "label": "COVID wave 1"},
                  {"from": "2021-04", "to": "2021-06", "label": "COVID wave 2"}],
        "lines": [{"month": SAME_ACCOUNT, "id": "SA", "label": "NPCI same-account exclusion"},
                  {"month": CARD_DEFINITION, "id": "C19", "label": "RBI card data definition change"},
                  {"month": ZERO_MDR, "id": "MDR", "label": "Zero MDR on UPI and RuPay debit"},
                  {"month": PSI_START, "id": "PSI", "label": "Card splice to RBI PSI"},
                  {"month": RUPAY_CREDIT_ON_UPI, "id": "RCU", "label": "RuPay credit on UPI"}],
    }


def headline(summary, h):
    by = {s["method"]: s for s in summary}
    d, i, c = by["Debit cards"], by["IMPS"], by["Credit cards"]
    return (f"Debit cards show absolute contraction: their 12-month average volume is {abs(d['drawdownPct']):.0f}% below "
            f"its peak (12 months to {label_month(d['peak'])}). IMPS is the boundary case at {d_word(i)} "
            f"({i['drawdownPct']:+.1f}% from its peak, 12 months to {label_month(i['peak'])}). Credit cards "
            f"{'kept growing but lost share' if c['label'] == SHARE_LOSS else c['label'].lower()} to UPI P2M "
            f"({c['shareChangePp']:+.1f} pp). Verdicts (Assignment 2 rule / v2 test): H1 {h['H1']['a2']} / {h['H1']['v2']}; "
            f"H2 {h['H2']['a2']} / {h['H2']['v2']}; H3 {h['H3']['a2']} / {h['H3']['v2']}.")


def d_word(s):
    return "absolute contraction" if s["label"] == CONTRACTION else s["label"].lower()


def numbers(summary, h, mags, corr):
    rows = []
    for s in summary:
        rows += [{"name": f"{s['method']} drawdown from peak 12-month average", "value": s["drawdownPct"], "unit": "%"},
                 {"name": f"{s['method']} share change vs reference ({s['group']})", "value": s["shareChangePp"], "unit": "pp"},
                 {"name": f"{s['method']} classification (10%)", "value": s["label"], "unit": ""},
                 {"name": f"{s['method']} robustness agreement", "value": f"{s['agree']} of {s['variants']}", "unit": ""}]
    for k, v in h.items():
        rows += [{"name": f"{k} verdict, Assignment 2 rule", "value": v["a2"], "unit": ""},
                 {"name": f"{k} verdict, v2 test", "value": v["v2"], "unit": ""}]
    rows += [{"name": m["name"], "value": m["value"], "unit": m["unit"]} for m in mags]
    rows += [{"name": "Months, Nov 2017 window", "value": len(month_range(START, END)), "unit": "months"},
             {"name": "Months, P2M window", "value": len(month_range(P2M_START, END)), "unit": "months"}]
    return rows


def findings_md(head, summary, h, covid, corr):
    lines = ["# Final analysis: findings", "",
             f"Generated by `analysis/final_analysis.py`. Window {label_month(START)} – {label_month(END)}.", "",
             "## Headline", "", head, "", "## Classification (10% rule, primary groups)", "",
             "| Method | Group | Drawdown | Peak (12 months to) | Share change | Label | Agreement |",
             "|---|---|---|---|---|---|---|"]
    for s in summary:
        lines.append(f"| {s['method']} | {s['group']} | {s['drawdownPct']:+.1f}% | {label_month(s['peak'])} | "
                     f"{s['shareChangePp']:+.1f} pp | {s['label']} | {s['agree']} of {s['variants']} |")
    lines += ["", "## Hypotheses", "", "| | Assignment 2 rule | v2 test |", "|---|---|---|"]
    lines += [f"| {k} | {v['a2']} | {v['v2']} |" for k, v in h.items()]
    lines += ["", "## Corrections to Assignment 2", ""]
    lines += [f"- **{c['item']}.** A2: {c['a2']}. Repository: {c['repo']}." for c in corr]
    (OUTPUT / "tp_findings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run():
    P, splice, split, bank, bank_level = build_panel()
    class_rows, summary, paths, grid = classification(P)
    decomp = decomposition(P)
    T = transformed(P)
    trend_rows = trend_tests(P, T)
    diag = diagnostics(T)
    breaks, h2 = h2_tests(P, T)
    h1_rows, h1 = h1_tests(trend_rows)
    h3 = h3_tests(P, trend_rows)
    covid = covid_periods(P)
    mags = magnitudes(P, summary)
    sens = sensitivity(P, split)
    bridge_rows, bridge_summary, factors = bridge_checks(P, splice, bank, bank_level)
    corr = corrections(bridge_summary, split)
    verdicts = {"H1": h1, "H2": {"a2": h2["a2"], "v2": h2["v2"]}, "H3": {"a2": h3["a2"], "v2": h3["v2"]}}
    head = headline(summary, verdicts)

    panel_rows = []
    for m in month_range(START, END):
        row = {"month": m}
        for k in RAILS:
            row.update({f"{k}_volume_mn": P[f"{k}_n"].get(m), f"{k}_value_crore": P[f"{k}_v"].get(m),
                        f"{k}_real_value_crore": P[f"{k}_rv"].get(m), f"{k}_real_ticket_rs": P[f"{k}_t"].get(m)})
        for g in GROUPS:
            for k in GROUPS[g][1]:
                row[f"share_{k}_{g}_pct"] = 100 * P[f"sh_{k}_{g}_n"][m] if m in P[f"sh_{k}_{g}_n"] else None
        row.update({"credit_cards_lakh": P["credit_cards"].get(m), "debit_cards_lakh": P["debit_cards"].get(m),
                    "pos_terminals_lakh": P["pos"].get(m), "debit_atm_withdrawals_mn": P["debit_atm_n"].get(m),
                    "diwali": int(m in DIWALI), "covid": int(m in COVID_MONTHS)})
        panel_rows.append(row)
    write_csv("tp_panel.csv", panel_rows)
    write_csv("tp_classification.csv", class_rows)
    write_csv("tp_classification_grid.csv", grid)
    write_csv("tp_share_decomposition.csv", decomp)
    write_csv("tp_trend_tests.csv", trend_rows)
    write_csv("tp_diagnostics.csv", diag)
    write_csv("tp_breaks.csv", breaks)
    write_csv("tp_h1.csv", h1_rows)
    write_csv("tp_h3_decomposition.csv", h3["decomposition"])
    write_csv("tp_covid_periods.csv", flatten_covid(covid))
    write_csv("tp_magnitudes.csv", mags)
    write_csv("tp_sensitivity.csv", sens)
    write_csv("tp_bridge_checks.csv", bridge_rows)
    write_csv("tp_hypotheses.csv", [{"hypothesis": k, "a2Rule": v["a2"], "v2Test": v["v2"]} for k, v in verdicts.items()])
    write_csv("tp_numbers.csv", numbers(summary, verdicts, mags, corr))
    findings_md(head, summary, verdicts, covid, corr)

    data = {"window": {"start": START, "end": END, "p2mStart": P2M_START, "months": len(month_range(START, END)),
                       "p2mMonths": len(month_range(P2M_START, END)), "dailyEnd": max(P["imps_n_daily"])},
            "headline": head, "summary": summary, "grid": grid, "classification": class_rows,
            "decomposition": decomp, "covid": covid, "h1": {"rows": h1_rows, **h1}, "h2": {"rows": breaks, **h2},
            "h3": h3, "verdicts": verdicts, "magnitudes": mags, "sensitivity": sens,
            "bridge": {"summary": bridge_summary, "factors": factors}, "corrections": corr,
            "diagnostics": diag, "trend": [x for x in trend_rows if x["method"].startswith(("Drift:", "A2 OLS trend", "Segmented"))],
            "series": page_series(P, paths)}
    DOCS_DATA.write_text("window.FINAL_DATA = " + json.dumps(data, ensure_ascii=False, default=str,
                                                              separators=(",", ":")) + ";\n", encoding="utf-8")
    print(head)
    return data


if __name__ == "__main__":
    run()
