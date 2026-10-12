#!/usr/bin/env python3
"""Forecast the payment mix to FY2029-30, backtest it, and set up policy scenario levers.

Run from any directory after payment_mix_model.py (it reads that script's monthly panel):
    python3 analysis/forecast_model.py

Chosen model ("combo", selected by the out-of-sample backtest below among 11 candidates):
an equal-weight geometric average of two components.
  * Logit share model. Merchant basket M = UPI P2M + credit + debit and transfer basket
    T = UPI P2P + IMPS follow damped-trend exponential smoothing (ETS A,Ad,N) on the log of the
    seasonally adjusted monthly volume. Shares are a multinomial logit with UPI as the base
    alternative: the log-odds z_k = ln(N_k / N_UPI) of each competing rail follows its own
    damped-trend ETS, and s_k = exp(z_k) / (1 + sum_j exp(z_j)).
  * Logistic diffusion for the UPI rails (an S-curve with an estimated saturation level, i.e. a
    Bass curve without the innovation term) and per-rail damped ETS for cards and IMPS.
  Neither component wins on every rail (the share model is best on cards, diffusion on UPI P2M);
  the average beats both on rolling 12-month error and on share error.
  Scenario levers act on the combined forecast's log-odds (utilities relative to UPI), so their
  effects on shares are coherent and add up to 100%.
  * Uncertainty. 1,000 simulated paths of the share model's five state-space equations, driven by
    a joint 6-month block bootstrap of their one-step residuals (keeps cross-rail correlation and
    short-run persistence), re-centred month by month on the combined point forecast in
    log-odds space. Residuals are scaled by kappa so that the 80% intervals cover 12-month-ahead
    outcomes in the rolling backtest; second-year coverage is reported as a diagnostic.

Estimation window: June 2021 - March 2026, UPI's mature phase with consistently classified RBI
card data (the study's primary sample). The backtest refits every model at each origin using only
data up to that origin.

Outputs:
  analysis/output/forecast_base.csv              FY volumes, shares, tickets with 80%/95% intervals
  analysis/output/forecast_monthly.csv           monthly median and 80% interval to Mar 2030
  analysis/output/forecast_backtest.csv          every backtest forecast, actual and error
  analysis/output/forecast_backtest_summary.csv  MAPE by model (the model-selection table)
  analysis/output/forecast_scenarios.csv         preset scenarios, FY volumes and shares
  analysis/output/forecast_assumptions.csv       lever assumptions, calibrated vs assumed
  docs/forecast-data.js                          data for docs/play.html

These are projections conditional on the model and the stated assumptions, not predictions.
National aggregates do not identify substitution elasticities; levers whose elasticity is not
estimable are labelled as assumptions and the page lets the user vary them.
"""

from __future__ import annotations

import csv
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from payment_mix_model import END_MONTH, INPUT, OUTPUT, REPO, fiscal_year, month_range, shift_month  # noqa: E402
from hypothesis_tests import Z95, hac_se, ols  # noqa: E402

DOCS_DATA = REPO / "docs" / "forecast-data.js"
START = "2021-06"
HORIZON_END = "2030-03"
FORECAST_FYS = ["FY2026-27", "FY2027-28", "FY2028-29", "FY2029-30"]
RAILS = ["p2m", "credit", "debit", "p2p", "imps"]
NAMES = {"p2m": "UPI P2M", "credit": "Credit card", "debit": "Debit card", "p2p": "UPI P2P",
         "imps": "IMPS"}
PANEL_KEYS = {("merchant", "UPI P2M"): "p2m", ("merchant", "credit card"): "credit",
              ("merchant", "debit card"): "debit", ("upi_use", "UPI P2P"): "p2p",
              ("transfer", "IMPS"): "imps"}
LATENTS = ["logM", "zC", "zD", "logT", "zI"]
SEED = 20261012
N_SIM = 1000
N_SIM_BACKTEST = 400
BLOCK = 6
ALPHAS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
BETAS = [0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3]
PHIS = [0.80, 0.85, 0.90, 0.93, 0.95, 0.97, 0.98, 0.99, 1.0]
FIXED_ORIGINS = {"2024-03": ["FY2024-25", "FY2025-26"], "2025-03": ["FY2025-26"]}
ROLLING_ORIGINS = month_range("2024-03", "2025-03")
Z80 = 1.281552

# Scenario ramps: policy levers phase in over two years (full effect from FY2028-29);
# credit-on-UPI adoption rises linearly to its FY2029-30 target; the app cap binds from Jan 2027
# (NPCI's deadline is 31 Dec 2026), i.e. one quarter of FY2026-27.
POLICY_RAMP = [1 / 3, 2 / 3, 1.0, 1.0]
ADOPTION_RAMP = [0.25, 0.5, 0.75, 1.0]
CAP_RAMP = [0.25, 1.0, 1.0, 1.0]

# Fee assumptions for revenue pools (gross merchant discount, % of value). Stated, not estimated.
FEES = {
    "credit": {"rate": 1.8, "status": "Assumed",
               "note": "Blended credit-card MDR; typical published Indian range 1.5–2.5% of value."},
    "debit": {"rate": 0.3, "status": "Assumed",
              "note": "Blended debit MDR: RBI caps (0.4%/0.9%, Dec 2017) on Visa/Mastercard, zero on RuPay debit since Jan 2020."},
    "p2m": {"rate": 0.0, "status": "Policy",
            "note": "Zero MDR on UPI since Jan 2020 (PSS Act s.10A); the MDR lever sets a rate on P2M payments above ₹2,000."},
}


# ---------- Data ----------

def cal(month: str) -> int:
    return int(month[5:7])


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def load() -> dict[str, object]:
    volume, value = defaultdict(dict), defaultdict(dict)
    for row in read_csv(OUTPUT / "monthly_panel.csv"):
        rail = PANEL_KEYS.get((row["basket"], row["rail"]))
        if rail:
            volume[rail][row["month"]] = float(row["volume_mn"])
            value[rail][row["month"]] = float(row["value_nominal_crore"])
    months = month_range(START, END_MONTH)
    for rail in RAILS:
        missing = [m for m in months if m not in volume[rail]]
        if missing:
            raise SystemExit(f"{rail}: monthly panel lacks {missing[:3]}; run payment_mix_model.py first")
    data = {"months": months,
            "vol": {r: [volume[r][m] for m in months] for r in RAILS},
            "val": {r: [value[r][m] for m in months] for r in RAILS}}

    infra = {row["month"]: row for row in read_csv(OUTPUT / "infrastructure_panel.csv")}
    data["infra"] = {k: [float(infra[m][k]) for m in months] for k in (
        "pos_terminals_lakh", "upi_qr_codes_lakh", "credit_cards_outstanding_lakh",
        "debit_cards_outstanding_lakh")}

    # Long history for context (spliced cards from 2014, UPI/IMPS from 2016, P2M from 2020).
    sums: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in read_csv(OUTPUT / "long_series_panel.csv"):
        fy = fiscal_year(row["month"])
        fields = {"credit": row["credit_n"], "debit": row["debit_n"], "imps": row["imps_n"],
                  "upi": row["upi_n"], "p2m": row["p2m_n"], "credit_v": row["credit_v"],
                  "debit_v": row["debit_v"]}
        for key, raw in fields.items():
            if raw not in ("", None):
                sums[fy][key].append(float(raw))
    history = []
    for fy in sorted(sums):
        rec = {"fy": fy}
        for key, values in sums[fy].items():
            if len(values) == 12:
                rec[key] = round(sum(values), 3)
        if "upi" in rec and "p2m" in rec:
            rec["p2p"] = round(rec["upi"] - rec["p2m"], 3)
        if "upi" in rec and rec["upi"] <= 0:
            del rec["upi"]
        if len(rec) > 1:
            history.append(rec)
    data["history"] = history

    apps = [row for row in read_csv(_latest(INPUT, "npci_upi_apps.csv")) if row["month"] == END_MONTH]
    total = sum(float(a["volume_mn"]) for a in apps)
    data["apps"] = sorted(([a["app"], 100 * float(a["volume_mn"]) / total] for a in apps),
                          key=lambda pair: -pair[1])
    data["breaks"] = {row["series"]: row for row in read_csv(OUTPUT / "structural_breaks.csv")}
    return data


def _latest(root: Path, filename: str) -> Path:
    return sorted(root.glob(f"*/{filename}"))[-1]


# ---------- Building blocks ----------

def seasonal_factors(x: list[float], months: list[str]) -> dict[int, float]:
    """Additive monthly factors: mean deviation from a centred 2x12 moving average, centred to 0."""
    dev = defaultdict(list)
    for t in range(6, len(x) - 6):
        ma = (0.5 * x[t - 6] + sum(x[t - 5:t + 6]) + 0.5 * x[t + 6]) / 12
        dev[cal(months[t])].append(x[t] - ma)
    s = {m: (sum(dev[m]) / len(dev[m]) if dev[m] else 0.0) for m in range(1, 13)}
    mu = sum(s.values()) / 12
    return {m: s[m] - mu for m in s}


def holt_run(x: list[float], a: float, b: float, phi: float):
    k = min(12, len(x) - 1)
    level, trend = x[0], (x[k] - x[0]) / k
    resid = []
    for t in range(1, len(x)):
        fit = level + phi * trend
        e = x[t] - fit
        resid.append(e)
        level = fit + a * e
        trend = phi * trend + b * e
    return level, trend, resid


def multistep_sse(x: list[float], a: float, b: float, phi: float) -> float:
    """Sum of squared 1-, 3-, 6- and 12-step-ahead in-sample errors."""
    k = min(12, len(x) - 1)
    level, trend = x[0], (x[k] - x[0]) / k
    sse = 0.0
    for t in range(1, len(x)):
        acc, power = 0.0, 1.0
        for h in range(1, 13):
            power *= phi
            acc += power
            if h in (1, 3, 6, 12) and t - 1 + h < len(x):
                sse += (x[t - 1 + h] - level - trend * acc) ** 2
        fit = level + phi * trend
        e = x[t] - fit
        level = fit + a * e
        trend = phi * trend + b * e
    return sse


def holt_fit(x: list[float], phis: list[float] = PHIS, criterion: str = "one") -> dict[str, object]:
    """Damped-trend ETS(A,Ad,N), parameters by grid search on one-step (or multi-step) squared error."""
    best = None
    for a in ALPHAS:
        for b in BETAS:
            if b > a:
                continue
            for phi in phis:
                level, trend, resid = holt_run(x, a, b, phi)
                sse = (sum(e * e for e in resid) if criterion == "one"
                       else multistep_sse(x, a, b, phi))
                if best is None or sse < best["sse"]:
                    best = {"sse": sse, "alpha": a, "beta": b, "phi": phi, "level": level,
                            "trend": trend, "resid": resid}
    return best


def holt_path(fit: dict[str, object], horizon: int) -> list[float]:
    out, acc, power = [], 0.0, 1.0
    for _ in range(horizon):
        power *= fit["phi"]
        acc += power
        out.append(fit["level"] + fit["trend"] * acc)
    return out


def fit_latent(x: list[float], months: list[str], phis: list[float] = PHIS,
               criterion: str = "one") -> dict[str, object]:
    season = seasonal_factors(x, months)
    fit = holt_fit([v - season[cal(m)] for v, m in zip(x, months)], phis, criterion)
    fit["season"] = season
    return fit


def latent_forecast(fit: dict[str, object], future: list[str]) -> list[float]:
    return [p + fit["season"][cal(m)] for p, m in zip(holt_path(fit, len(future)), future)]


def latents(vol: dict[str, list[float]]) -> dict[str, list[float]]:
    p, c, d, q, i = (vol[r] for r in RAILS)
    return {"logM": [math.log(a + b + e) for a, b, e in zip(p, c, d)],
            "zC": [math.log(b / a) for a, b in zip(p, c)],
            "zD": [math.log(e / a) for a, e in zip(p, d)],
            "logT": [math.log(a + b) for a, b in zip(q, i)],
            "zI": [math.log(b / a) for a, b in zip(q, i)]}


def recompose(lat: dict[str, float]) -> dict[str, float]:
    m, t = math.exp(lat["logM"]), math.exp(lat["logT"])
    ec, ed, ei = math.exp(lat["zC"]), math.exp(lat["zD"]), math.exp(lat["zI"])
    den = 1 + ec + ed
    return {"p2m": m / den, "credit": m * ec / den, "debit": m * ed / den,
            "p2p": t / (1 + ei), "imps": t * ei / (1 + ei)}


# ---------- Candidate models (each returns monthly level forecasts per rail) ----------

def m_naive_level(vol, months, future):
    return {r: [vol[r][len(vol[r]) - 12 + h % 12] for h in range(len(future))] for r in RAILS}


def m_naive_growth(vol, months, future):
    out = {}
    for r in RAILS:
        g = sum(vol[r][-12:]) / sum(vol[r][-24:-12])
        out[r] = [vol[r][len(vol[r]) - 12 + h % 12] * g ** (h // 12 + 1) for h in range(len(future))]
    return out


def m_loglinear(vol, months, future):
    out = {}
    for r in RAILS:
        x = [math.log(v) for v in vol[r]]
        season = seasonal_factors(x, months)
        d = [v - season[cal(m)] for v, m in zip(x, months)][-24:]
        fit = ols(d, [[1.0, float(t)] for t in range(len(d))])
        a, b = fit["beta"]
        out[r] = [math.exp(a + b * (len(d) - 1 + h) + season[cal(m)])
                  for h, m in enumerate(future, start=1)]
    return out


def m_holt(vol, months, future):
    out = {}
    for r in RAILS:
        fit = fit_latent([math.log(v) for v in vol[r]], months)
        out[r] = [math.exp(v) for v in latent_forecast(fit, future)]
    return out


def logistic_fit(y: list[float]) -> tuple[float, float, float]:
    """Logistic diffusion y_t = K / (1 + exp(-(a + b t))) on log scale; K by grid, (a, b) by OLS."""
    best, top = None, max(y)
    t_axis = [[1.0, float(t)] for t in range(len(y))]
    for i in range(90):
        k = top * 1.01 * (40 / 1.01) ** (i / 89)
        z = [math.log(v / (k - v)) for v in y]
        a, b = ols(z, t_axis)["beta"]
        sse = sum((math.log(v) - (math.log(k) - math.log1p(math.exp(-(a + b * t))))) ** 2
                  for t, v in enumerate(y))
        if best is None or sse < best[0]:
            best = (sse, k, a, b)
    return best[1:]


def m_logistic(vol, months, future):
    out = m_holt(vol, months, future)
    for r in ("p2m", "p2p"):
        x = [math.log(v) for v in vol[r]]
        season = seasonal_factors(x, months)
        y = [math.exp(v - season[cal(m)]) for v, m in zip(x, months)]
        k, a, b = logistic_fit(y)
        n = len(y)
        out[r] = [k / (1 + math.exp(-(a + b * (n - 1 + h)))) * math.exp(season[cal(m)])
                  for h, m in enumerate(future, start=1)]
    return out


def debit_decay(vol, months, future) -> list[float]:
    """Exponential decay to a floor: D_t = F + A exp(-k t), F by grid, ln(D - F) by OLS."""
    x = [math.log(v) for v in vol["debit"]]
    season = seasonal_factors(x, months)
    y = [math.exp(v - season[cal(m)]) for v, m in zip(x, months)]
    best, t_axis = None, [[1.0, float(t)] for t in range(len(y))]
    for j in range(31):
        floor = min(y) * 0.98 * j / 30
        c, b = ols([math.log(v - floor) for v in y], t_axis)["beta"]
        sse = sum((math.log(v) - math.log(floor + math.exp(c + b * t))) ** 2 for t, v in enumerate(y))
        if best is None or sse < best[0]:
            best = (sse, floor, c, b)
    _, floor, c, b = best
    n = len(y)
    return [(floor + math.exp(c + b * (n - 1 + h))) * math.exp(season[cal(m)])
            for h, m in enumerate(future, start=1)]


def share_fits(vol, months, damped=True, criterion="one"):
    lat = latents(vol)
    return {k: fit_latent(lat[k], months, PHIS if (damped or k.startswith("log")) else [1.0], criterion)
            for k in LATENTS}


def logistic_latent(x: list[float], months: list[str], future: list[str]) -> list[float]:
    """Log basket total from a logistic diffusion curve fitted to the seasonally adjusted level."""
    season = seasonal_factors(x, months)
    k, a, b = logistic_fit([math.exp(v - season[cal(m)]) for v, m in zip(x, months)])
    n = len(x)
    return [math.log(k) - math.log1p(math.exp(-(a + b * (n - 1 + h)))) + season[cal(m)]
            for h, m in enumerate(future, start=1)]


def m_share(vol, months, future, damped=True, criterion="one", totals="ets"):
    fits = share_fits(vol, months, damped, criterion)
    paths = {k: latent_forecast(fits[k], future) for k in LATENTS}
    if totals == "logistic":
        lat = latents(vol)
        for k in ("logM", "logT"):
            paths[k] = logistic_latent(lat[k], months, future)
    out = {r: [] for r in RAILS}
    for h in range(len(future)):
        rails = recompose({k: paths[k][h] for k in LATENTS})
        for r in RAILS:
            out[r].append(rails[r])
    return out


def m_share_linear(vol, months, future):
    return m_share(vol, months, future, damped=False)


def m_share_multistep(vol, months, future):
    return m_share(vol, months, future, criterion="multi")


def m_share_logistic(vol, months, future):
    return m_share(vol, months, future, totals="logistic")


def geometric_mean(parts: list[dict[str, list[float]]]) -> dict[str, list[float]]:
    return {r: [math.exp(sum(math.log(p[r][h]) for p in parts) / len(parts))
                for h in range(len(parts[0][r]))] for r in RAILS}


def m_ensemble(vol, months, future):
    return geometric_mean([m_share(vol, months, future), m_holt(vol, months, future)])


def m_combo(vol, months, future):
    return geometric_mean([m_share(vol, months, future), m_logistic(vol, months, future)])


def m_combo3(vol, months, future):
    return geometric_mean([m_share(vol, months, future), m_logistic(vol, months, future),
                           m_holt(vol, months, future)])


MODELS = [
    ("naive_level", "Naive: next year = last year", m_naive_level),
    ("naive_growth", "Naive: last year's growth repeats", m_naive_growth),
    ("loglinear", "Constant growth (log-linear trend, last 24 months)", m_loglinear),
    ("logistic", "Logistic diffusion for UPI rails (ETS for cards, IMPS)", m_logistic),
    ("holt", "Damped-trend ETS per rail (log volumes)", m_holt),
    ("share_linear", "Share model, undamped (linear) log-odds trend", m_share_linear),
    ("share_damped", "Share model: damped ETS totals × damped log-odds (logit)", m_share),
    ("share_multistep", "Share model, ETS fitted on 1–12-month-ahead error", m_share_multistep),
    ("share_logistic", "Share model: logistic-diffusion totals × damped log-odds (logit)", m_share_logistic),
    ("ensemble", "Average of share model and per-rail ETS", m_ensemble),
    ("combo", "Average of share model and logistic diffusion", m_combo),
    ("combo3", "Average of share model, logistic diffusion and per-rail ETS", m_combo3),
]
MODELS_BY_KEY = {k: fn for k, _, fn in MODELS}
CHOSEN = "combo"


# ---------- Simulation ----------

def point_latents(vol, months, future) -> dict[str, list[float]]:
    """Latent paths (log totals, log-odds) of the chosen model's point forecast."""
    fc = dict(MODELS_BY_KEY[CHOSEN](vol, months, future))
    return latents(fc)


def simulate(vol, months, horizon, n_sim, kappa, rng) -> list[dict[str, list[float]]]:
    """Joint block-bootstrap simulation of the five latent ETS models of the share model.

    The simulated latents are re-centred, month by month, on the chosen model's point forecast:
    the share model supplies the shape and cross-rail correlation of the uncertainty, the
    combination supplies the centre. Returns rail paths.
    """
    fits = share_fits(vol, months)
    future = [shift_month(months[-1], h) for h in range(1, horizon + 1)]
    centre = point_latents(vol, months, future)
    resid = {k: fits[k]["resid"] for k in LATENTS}
    for k in LATENTS:
        mu = sum(resid[k]) / len(resid[k])
        resid[k] = [e - mu for e in resid[k]]
    n_res = len(resid[LATENTS[0]])
    sims = []
    for _ in range(n_sim):
        state = {k: [fits[k]["level"], fits[k]["trend"]] for k in LATENTS}
        path = {k: [] for k in LATENTS}
        idx = rng.randrange(n_res)
        for h in range(horizon):
            if h % BLOCK == 0:
                idx = rng.randrange(n_res - BLOCK + 1)
            else:
                idx += 1
            for k in LATENTS:
                f = fits[k]
                level, trend = state[k]
                e = kappa * resid[k][idx]
                fit = level + f["phi"] * trend
                path[k].append(fit + e)
                state[k] = [fit + f["alpha"] * e, f["phi"] * trend + f["beta"] * e]
        sims.append(path)
    shift = {k: [centre[k][h] - quantile([s[k][h] for s in sims], .5) for h in range(horizon)]
             for k in LATENTS}
    draws = []
    for s in sims:
        path = {r: [] for r in RAILS}
        for h in range(horizon):
            rails = recompose({k: s[k][h] + shift[k][h] for k in LATENTS})
            for r in RAILS:
                path[r].append(rails[r])
        draws.append(path)
    return draws


def quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


# ---------- Backtest ----------

def fy_sum(series: list[float], months: list[str], fy: str) -> float:
    picked = [v for v, m in zip(series, months) if fiscal_year(m) == fy]
    if len(picked) != 12:
        raise ValueError(f"{fy} incomplete")
    return sum(picked)


def backtest(data) -> tuple[list[dict], list[dict], float, dict]:
    months, vol = data["months"], data["vol"]
    rows = []
    for origin, targets in FIXED_ORIGINS.items():
        cut = months.index(origin) + 1
        train = {r: vol[r][:cut] for r in RAILS}
        future = months[cut:]
        actual_fy = {fy: {r: fy_sum(vol[r], months, fy) for r in RAILS} for fy in targets}
        for key, label, fn in MODELS:
            fc = fn(train, months[:cut], future)
            for fy in targets:
                fc_fy = {r: fy_sum(fc[r], future, fy) for r in RAILS}
                for r in RAILS:
                    rows.append(_bt_row("fixed", key, origin, fy, r, fc_fy[r], actual_fy[fy][r],
                                        fc_fy, actual_fy[fy]))
            if key == "share_damped":
                dd = debit_decay(train, months[:cut], future)
                for fy in targets:
                    f = fy_sum(dd, future, fy)
                    rows.append(_bt_row("fixed", "debit_decay", origin, fy, "debit", f,
                                        actual_fy[fy]["debit"], None, None))
    for origin in ROLLING_ORIGINS:
        cut = months.index(origin) + 1
        train = {r: vol[r][:cut] for r in RAILS}
        future = months[cut:cut + 12]
        actual = {r: sum(vol[r][cut:cut + 12]) for r in RAILS}
        target = f"12 months to {future[-1]}"
        for key, label, fn in MODELS:
            fc = fn(train, months[:cut], future)
            fc12 = {r: sum(fc[r]) for r in RAILS}
            for r in RAILS:
                rows.append(_bt_row("rolling", key, origin, target, r, fc12[r], actual[r], fc12, actual))

    summary = []
    labels = dict((k, l) for k, l, _ in MODELS)
    labels["debit_decay"] = "Debit decay curve (exponential to a floor), debit only"
    for key in [k for k, _, _ in MODELS] + ["debit_decay"]:
        fixed = [r for r in rows if r["model"] == key and r["design"] == "fixed"]
        rolling = [r for r in rows if r["model"] == key and r["design"] == "rolling"]
        rec = {"model": key, "label": labels[key], "chosen": key == CHOSEN}
        rec["mape_fixed"] = _mean(r["ape_pct"] for r in fixed)
        for origin, targets in FIXED_ORIGINS.items():
            for fy in targets:
                rec[f"mape_{origin}_{fy}"] = _mean(r["ape_pct"] for r in fixed
                                                   if r["origin"] == origin and r["target"] == fy)
        rec["mape_rolling12"] = _mean(r["ape_pct"] for r in rolling) if rolling else None
        for rail in RAILS:
            rec[f"mape_fixed_{rail}"] = _mean(r["ape_pct"] for r in fixed if r["rail"] == rail)
        rec["share_mae_pp"] = _mean(r["share_abs_err_pp"] for r in fixed + rolling
                                    if r["share_abs_err_pp"] is not None)
        summary.append(rec)

    # Interval calibration: kappa scales the bootstrapped residuals so that the standardised
    # 12-month-ahead errors (13 rolling origins x 5 rails) have unit root mean square. Second-year
    # sums (months 13-24 after origins Jun 2023 - Mar 2024) are a diagnostic only: their misses
    # come mostly from point bias (UPI overshoot), and scaling every horizon by them would make
    # the FY2029-30 bands uninformative.
    rng = random.Random(SEED + 1)

    def interval_check(origins, start, kappa_value):
        standardised, covered = [], []
        for origin in origins:
            cut = months.index(origin) + 1
            train = {r: vol[r][:cut] for r in RAILS}
            draws = simulate(train, months[:cut], start + 12, N_SIM_BACKTEST, kappa_value, rng)
            for r in RAILS:
                sums = [sum(d[r][start:start + 12]) for d in draws]
                lo, med, hi = quantile(sums, .1), quantile(sums, .5), quantile(sums, .9)
                act = sum(vol[r][cut + start:cut + start + 12])
                standardised.append((math.log(act) - math.log(med)) / (math.log(hi / lo) / (2 * Z80)))
                covered.append(lo <= act <= hi)
        rms = math.sqrt(sum(u * u for u in standardised) / len(standardised))
        return rms, 100 * sum(covered) / len(covered), len(covered)

    second_year_origins = month_range("2023-06", "2024-03")
    rms12, cov12_raw, n12 = interval_check(ROLLING_ORIGINS, 0, 1.0)
    rms24, cov24_raw, n24 = interval_check(second_year_origins, 12, 1.0)
    kappa = max(1.0, rms12)
    _, cov12_k, _ = interval_check(ROLLING_ORIGINS, 0, kappa)
    _, cov24_k, _ = interval_check(second_year_origins, 12, kappa)
    # Two-year-ahead check from Mar 2024 (FY2025-26), with kappa.
    cut = months.index("2024-03") + 1
    train = {r: vol[r][:cut] for r in RAILS}
    future = months[cut:]
    draws = simulate(train, months[:cut], len(future), N_SIM_BACKTEST, kappa, rng)
    two_year = {}
    for r in RAILS:
        sums = [fy_sum(d[r], future, "FY2025-26") for d in draws]
        act = fy_sum(vol[r], months, "FY2025-26")
        two_year[r] = {"lo80": quantile(sums, .1), "median": quantile(sums, .5),
                       "hi80": quantile(sums, .9), "actual": act,
                       "covered": quantile(sums, .1) <= act <= quantile(sums, .9)}
    calibration = {"kappa": kappa, "rms12": rms12, "rms24": rms24,
                   "n12": n12, "n24": n24,
                   "coverage80_12m_raw": cov12_raw, "coverage80_12m_calibrated": cov12_k,
                   "coverage80_24m_raw": cov24_raw, "coverage80_24m_calibrated": cov24_k,
                   "two_year_from_2024_03": two_year}
    return rows, summary, kappa, calibration


def _bt_row(design, model, origin, target, rail, forecast, actual, fc_all, act_all):
    share_err = None
    if fc_all is not None:
        basket = ["p2m", "credit", "debit"] if rail in ("p2m", "credit", "debit") else ["p2p", "imps"]
        fs = forecast / sum(fc_all[b] for b in basket)
        as_ = actual / sum(act_all[b] for b in basket)
        share_err = 100 * abs(fs - as_)
    return {"design": design, "model": model, "origin": origin, "target": target, "rail": rail,
            "forecast_mn": forecast, "actual_mn": actual,
            "error_pct": 100 * (forecast - actual) / actual,
            "ape_pct": 100 * abs(forecast - actual) / actual, "share_abs_err_pp": share_err}


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else None


# ---------- Final forecast ----------

def final_forecast(data, kappa):
    months, vol = data["months"], data["vol"]
    future = month_range(shift_month(END_MONTH, 1), HORIZON_END)
    rng = random.Random(SEED)
    draws = simulate(vol, months, len(future), N_SIM, kappa, rng)
    fits = share_fits(vol, months)

    # Tickets (nominal ₹ per transaction): damped ETS on log ticket, point paths only.
    tickets = {}
    for r in RAILS:
        x = [math.log(v * 10 / n) for v, n in zip(data["val"][r], vol[r])]
        tickets[r] = [math.exp(v) for v in latent_forecast(fit_latent(x, months), future)]

    monthly = []
    for h, m in enumerate(future):
        for r in RAILS:
            vals = [d[r][h] for d in draws]
            monthly.append({"month": m, "rail": r, "median_mn": quantile(vals, .5),
                            "lo80_mn": quantile(vals, .1), "hi80_mn": quantile(vals, .9)})

    annual_draws = []
    for d in draws:
        annual_draws.append([[fy_sum(d[r], future, fy) for r in RAILS] for fy in FORECAST_FYS])

    med_paths = {r: [quantile([d[r][h] for d in draws], .5) for h in range(len(future))] for r in RAILS}
    fy_ticket = {}
    for fy in FORECAST_FYS:
        fy_ticket[fy] = {}
        for r in RAILS:
            idx = [h for h, m in enumerate(future) if fiscal_year(m) == fy]
            vol_fy = sum(med_paths[r][h] for h in idx)
            fy_ticket[fy][r] = sum(med_paths[r][h] * tickets[r][h] for h in idx) / vol_fy
    params = {k: {"alpha": fits[k]["alpha"], "beta": fits[k]["beta"], "phi": fits[k]["phi"]}
              for k in LATENTS}
    return {"future": future, "draws": draws, "annual_draws": annual_draws, "monthly": monthly,
            "fy_ticket": fy_ticket, "params": params}


def summarise_annual(annual_draws, fy_ticket):
    rows = []
    for i, fy in enumerate(FORECAST_FYS):
        per = [d[i] for d in annual_draws]
        for j, r in enumerate(RAILS):
            vols = [p[j] for p in per]
            basket = [0, 1, 2] if j < 3 else [3, 4]
            shares = [100 * p[j] / sum(p[b] for b in basket) for p in per]
            rec = {"fiscal_year": fy, "rail": NAMES[r], "basket": "merchant" if j < 3 else "transfer"}
            for name, q in (("median", .5), ("lo80", .1), ("hi80", .9), ("lo95", .025), ("hi95", .975)):
                rec[f"volume_mn_{name}"] = quantile(vols, q)
                rec[f"share_pct_{name}"] = quantile(shares, q)
            rec["avg_ticket_rs"] = fy_ticket[fy][r]
            rec["value_crore_median"] = rec["volume_mn_median"] * fy_ticket[fy][r] / 10
            rows.append(rec)
    return rows


# ---------- Scenario levers (mirrored exactly in docs/play.html) ----------

def lever_defaults(data) -> dict[str, float]:
    infra, months = data["infra"], data["months"]
    i12 = months.index(shift_month(END_MONTH, -12))

    def cagr(key):
        # Latest year: UPI QR codes nearly doubled in FY2024-25, a one-off a 24-month CAGR would extrapolate.
        return 100 * (infra[key][-1] / infra[key][i12] - 1)

    return {"mdr": 0.0, "mdr_value_share": 50.0, "mdr_card_overlap": 50.0,
            "beta_mdr": round(_mdr_anchor(data), 2),
            "cou": 0.0, "cannibal": 30.0,
            "cards_growth": round(cagr("credit_cards_outstanding_lakh"), 1),
            "cards_growth_base": round(cagr("credit_cards_outstanding_lakh"), 1),
            "eps_cards": 0.5, "rewards": 0.0, "eta_rewards": 0.25,
            "pos_growth": round(cagr("pos_terminals_lakh"), 1),
            "pos_growth_base": round(cagr("pos_terminals_lakh"), 1),
            "qr_growth": round(cagr("upi_qr_codes_lakh"), 1),
            "qr_growth_base": round(cagr("upi_qr_codes_lakh"), 1),
            "b_acc": 0.3, "debit_shift": 0.0, "cap_on": 0, "cap_level": 30.0, "cap_friction": 0.0}


def _mdr_anchor(data) -> float:
    """Utility shift per 1 pp MDR implied by the Jan 2020 zero-MDR break in debit ÷ credit.

    structural_breaks.csv reports a +17.5% level shift in debit ÷ credit payments when MDR on
    RuPay debit (and UPI) went to zero. Treating that as a cut of roughly 0.5 pp in the blended debit
    MDR gives ln(1.175) / 0.5 ≈ 0.32 log-odds per pp. A known-date break, not a causal estimate.
    """
    row = data["breaks"].get("Debit ÷ credit payments (nets common shocks)")
    level = float(row["levelPct"]) if row else 17.5
    return math.log1p(level / 100) / 0.5


def capped_shares(shares: list[float], cap: float) -> list[float]:
    s = list(shares)
    for _ in range(50):
        excess = sum(max(0.0, v - cap) for v in s)
        if excess < 1e-9:
            break
        free = sum(v for v in s if v < cap)
        s = [cap if v >= cap else v + excess * v / free for v in s]
    return s


def hhi(shares: list[float]) -> float:
    return sum(v * v for v in shares)


def apply_levers(draw_fy: list[float], i: int, lv: dict[str, float], app_excess: float) -> dict[str, float]:
    p, c, d, q, imps = draw_fy
    m = p + c + d
    z_c, z_d = math.log(c / p), math.log(d / p)
    w, years = POLICY_RAMP[i], i + 1
    acc = years * (math.log1p(lv["pos_growth"] / 100) - math.log1p(lv["pos_growth_base"] / 100)
                   - math.log1p(lv["qr_growth"] / 100) + math.log1p(lv["qr_growth_base"] / 100))
    cards = years * (math.log1p(lv["cards_growth"] / 100) - math.log1p(lv["cards_growth_base"] / 100))
    # Steering happens where UPI and cards compete for the same payment: card payments above ₹2,000.
    mdr = lv["beta_mdr"] * lv["mdr"] * lv["mdr_card_overlap"] / 100 * w
    d_c = mdr + lv["eta_rewards"] * lv["rewards"] * w + lv["b_acc"] * acc + lv["eps_cards"] * cards
    d_d = mdr + lv["debit_shift"] * w + lv["b_acc"] * acc
    ec, ed = math.exp(z_c + d_c), math.exp(z_d + d_d)
    den = 1 + ec + ed
    p1, c1, d1 = m / den, m * ec / den, m * ed / den
    adopt = lv["cou"] / 100 * ADOPTION_RAMP[i]
    moved = min(lv["cannibal"] / 100 * adopt * p1, 0.9 * c1)
    c1 -= moved
    p1 += moved
    if lv["cap_on"]:
        loss = lv["cap_friction"] / 100 * app_excess / 100 * CAP_RAMP[i]
        p1 *= 1 - loss
        q *= 1 - loss
    return {"p2m": p1, "credit": c1, "debit": d1, "p2p": q, "imps": imps, "cou": adopt * p1}


PRESETS = {
    "base": ("Base: pre-MDR trends continue", {}),
    "mdr": ("Announced 0.4% MDR on UPI P2M above ₹2,000", {"mdr": 0.4}),
    "credit_upi": ("Credit on UPI takes off", {"cou": 5.0}),
    "issuer_push": ("Issuer push: more cards and richer rewards", {"cards_growth": 15.0, "rewards": 0.5}),
    "pos_stall": ("PoS stalls while UPI QR spreads", {"pos_growth": 0.0}),
    "cap": ("NPCI 30% app cap enforced", {"cap_on": 1}),
    "debit_revival": ("Debit revival (tap-to-pay incentives)", {"debit_shift": 0.5}),
}


def scenario_rows(annual_draws, defaults, app_excess):
    rows = []
    for key, (label, override) in PRESETS.items():
        lv = dict(defaults, **override)
        for i, fy in enumerate(FORECAST_FYS):
            outs = [apply_levers(d[i], i, lv, app_excess) for d in annual_draws]
            for r in RAILS:
                vols = [o[r] for o in outs]
                basket = ["p2m", "credit", "debit"] if r in ("p2m", "credit", "debit") else ["p2p", "imps"]
                shares = [100 * o[r] / sum(o[b] for b in basket) for o in outs]
                rows.append({"preset": key, "label": label, "fiscal_year": fy, "rail": NAMES[r],
                             "volume_mn_median": quantile(vols, .5), "volume_mn_lo80": quantile(vols, .1),
                             "volume_mn_hi80": quantile(vols, .9), "share_pct_median": quantile(shares, .5)})
    return rows


# ---------- Assumption register ----------

def assumption_rows(data, defaults):
    acc = _elasticity(data, "acc")
    cards = _elasticity(data, "cards")
    return [
        {"lever": "MDR on UPI P2M above ₹2,000", "parameter": "mdr", "default": defaults["mdr"],
         "unit": "% of value", "status": "Policy",
         "basis": "Zero since Jan 2020 (PSS Act s.10A). 0.4% on select P2M payments above ₹2,000 announced from 15 Oct 2026 (Ministry of Finance, 2026; NPCI MDR FAQ). The base case is the pre-MDR baseline; the preset applies 0.4%."},
        {"lever": "MDR on UPI P2M above ₹2,000", "parameter": "mdr_value_share", "default": defaults["mdr_value_share"],
         "unit": "% of P2M value above ₹2,000", "status": "Assumed",
         "basis": "Sets the fee pool. NPCI publishes no ticket-size split. Consistency check: 10% of P2M payments averaging ₹3,000 would be about 50% of value at the ₹608 average ticket."},
        {"lever": "MDR on UPI P2M above ₹2,000", "parameter": "mdr_card_overlap", "default": defaults["mdr_card_overlap"],
         "unit": "% of card payments above ₹2,000", "status": "Assumed",
         "basis": "Steering happens where UPI and cards compete for the same payment. Card average tickets are ₹3,700–3,800, but RBI publishes no distribution."},
        {"lever": "MDR on UPI P2M above ₹2,000", "parameter": "beta_mdr", "default": defaults["beta_mdr"],
         "unit": "log-odds per pp MDR", "status": "Anchored",
         "basis": "ln(1 + level shift) / 0.5 pp from the Jan 2020 zero-MDR break in debit ÷ credit payments (structural_breaks.csv, +17.5%). A known-date break, not a causal estimate."},
        {"lever": "Credit on UPI (RuPay credit cards, credit lines)", "parameter": "cou", "default": defaults["cou"],
         "unit": "% of P2M transactions by FY2029-30", "status": "Scenario",
         "basis": "NPCI does not publish credit-funded UPI volume monthly; 0 means no change from the trend already in the data."},
        {"lever": "Credit on UPI (RuPay credit cards, credit lines)", "parameter": "cannibal", "default": defaults["cannibal"],
         "unit": "% that would have been card swipes", "status": "Assumed",
         "basis": "Not identified in national aggregates."},
        {"lever": "Issuer push", "parameter": "cards_growth_base", "default": defaults["cards_growth_base"],
         "unit": "% per year", "status": "Calibrated",
         "basis": "Credit cards in force, growth Mar 2025 – Mar 2026 (RBI PSI Part III)."},
        {"lever": "Issuer push", "parameter": "eps_cards", "default": defaults["eps_cards"],
         "unit": "elasticity of credit log-odds to cards in force", "status": "Assumed",
         "basis": f"Our data do not identify it: 12-month changes in credit log-odds on card growth give {cards['b']:.2f} (HAC SE {cards['se']:.2f}, n={cards['n']}), the wrong sign because issuance and UPI growth move together. Default assumes new cards are used half as intensively as existing ones."},
        {"lever": "Issuer push", "parameter": "eta_rewards", "default": defaults["eta_rewards"],
         "unit": "log-odds per pp reward rate", "status": "Assumed",
         "basis": "No reward-rate data in our sources."},
        {"lever": "Merchant acceptance", "parameter": "pos_growth_base", "default": defaults["pos_growth_base"],
         "unit": "% per year", "status": "Calibrated", "basis": "PoS terminals, growth Mar 2025 – Mar 2026 (RBI PSI)."},
        {"lever": "Merchant acceptance", "parameter": "qr_growth_base", "default": defaults["qr_growth_base"],
         "unit": "% per year", "status": "Calibrated", "basis": "UPI QR codes, growth Mar 2025 – Mar 2026 (RBI PSI). The 24-month rate (48% a year) includes a one-off near-doubling in FY2024-25."},
        {"lever": "Merchant acceptance", "parameter": "b_acc", "default": defaults["b_acc"],
         "unit": "log-odds per log point of PoS ÷ QR", "status": "Assumed",
         "basis": f"Our data do not identify it: 12-month changes in credit log-odds on ln(PoS ÷ QR) give {acc['b']:.2f} (HAC SE {acc['se']:.2f}, n={acc['n']}), the wrong sign for a causal reading."},
        {"lever": "Debit card push or clean-up", "parameter": "debit_shift", "default": defaults["debit_shift"],
         "unit": "log-odds shift", "status": "Scenario",
         "basis": "Direct utility shift for debit. Removing inactive cards changes cards in force, not payments, so it is 0 unless it pushes users to UPI."},
        {"lever": "NPCI app market-share cap", "parameter": "cap_level", "default": defaults["cap_level"],
         "unit": "% of UPI volume per app", "status": "Policy",
         "basis": "NPCI circular (Nov 2020); compliance deadline extended to 31 Dec 2026. App shares from npci_upi_apps.csv, Mar 2026."},
        {"lever": "NPCI app market-share cap", "parameter": "cap_friction", "default": defaults["cap_friction"],
         "unit": "% of capped-out volume lost", "status": "Assumed",
         "basis": "Default 0: the cap moves users between apps, not off UPI."},
        {"lever": "Fees", "parameter": "credit MDR", "default": FEES["credit"]["rate"], "unit": "% of value",
         "status": FEES["credit"]["status"], "basis": FEES["credit"]["note"]},
        {"lever": "Fees", "parameter": "debit MDR", "default": FEES["debit"]["rate"], "unit": "% of value",
         "status": FEES["debit"]["status"], "basis": FEES["debit"]["note"]},
    ]


def _elasticity(data, kind: str) -> dict[str, float]:
    vol, infra = data["vol"], data["infra"]
    y, x = [], []
    for t in range(12, len(data["months"])):
        zc = math.log(vol["credit"][t] / vol["p2m"][t]) - math.log(vol["credit"][t - 12] / vol["p2m"][t - 12])
        if kind == "acc":
            ratio = [math.log(a / b) for a, b in zip(infra["pos_terminals_lakh"], infra["upi_qr_codes_lakh"])]
            x.append([1.0, ratio[t] - ratio[t - 12]])
        else:
            cc = infra["credit_cards_outstanding_lakh"]
            x.append([1.0, math.log(cc[t] / cc[t - 12])])
        y.append(zc)
    fit = ols(y, x)
    return {"b": fit["beta"][1], "se": hac_se(fit, x)[1], "n": len(y)}


# ---------- Output ----------

def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: (round(v, 6) if isinstance(v, float) else v) for k, v in row.items()})


def r1(x: float) -> float:
    return round(x, 1)


def main() -> None:
    data = load()
    bt_rows, summary, kappa, calibration = backtest(data)
    final = final_forecast(data, kappa)
    annual = summarise_annual(final["annual_draws"], final["fy_ticket"])
    defaults = lever_defaults(data)
    app_shares = [s for _, s in data["apps"]]
    app_excess = sum(max(0.0, s - defaults["cap_level"]) for s in app_shares)
    scenarios = scenario_rows(final["annual_draws"], defaults, app_excess)
    assumptions = assumption_rows(data, defaults)

    write_csv(OUTPUT / "forecast_base.csv", annual)
    write_csv(OUTPUT / "forecast_monthly.csv",
              [{"month": r["month"], "rail": NAMES[r["rail"]], "median_mn": r["median_mn"],
                "lo80_mn": r["lo80_mn"], "hi80_mn": r["hi80_mn"]} for r in final["monthly"]])
    write_csv(OUTPUT / "forecast_backtest.csv",
              [dict(r, rail=NAMES[r["rail"]]) for r in bt_rows])
    write_csv(OUTPUT / "forecast_backtest_summary.csv", summary)
    write_csv(OUTPUT / "forecast_scenarios.csv", scenarios)
    write_csv(OUTPUT / "forecast_assumptions.csv", assumptions)

    vol, months = data["vol"], data["months"]
    last_fy = {r: fy_sum(vol[r], months, "FY2025-26") for r in RAILS}
    last_val = {r: fy_sum(data["val"][r], months, "FY2025-26") for r in RAILS}
    infra_last = {k: v[-1] for k, v in data["infra"].items()}
    future = final["future"]
    model_views = []
    for key, label, fn in MODELS:
        fc = fn(vol, months, future)
        model_views.append({"model": key, "label": label,
                            "fy30": {r: r1(fy_sum(fc[r], future, "FY2029-30")) for r in RAILS}})
    payload = {
        "generated_from": "analysis/forecast_model.py",
        "data_end": END_MONTH,
        "fys": FORECAST_FYS,
        "rails": RAILS,
        "names": NAMES,
        "history": data["history"],
        "last_fy": {"fy": "FY2025-26", "volume": {r: r1(v) for r, v in last_fy.items()},
                    "value_crore": {r: round(v) for r, v in last_val.items()}},
        "draws": [[[r1(v) for v in fy] for fy in d] for d in final["annual_draws"]],
        "tickets": {fy: {r: r1(v) for r, v in t.items()} for fy, t in final["fy_ticket"].items()},
        "infra_last": {k: r1(v) for k, v in infra_last.items()},
        "apps": [[name, round(s, 3)] for name, s in data["apps"]],
        "app_hhi": round(hhi(app_shares)),
        "defaults": defaults,
        "presets": {k: {"label": label, "set": override} for k, (label, override) in PRESETS.items()},
        "ramps": {"policy": POLICY_RAMP, "adoption": ADOPTION_RAMP, "cap": CAP_RAMP},
        "fees": FEES,
        "assumptions": assumptions,
        "params": final["params"],
        "kappa": round(kappa, 3),
        "calibration": calibration,
        "chosen": CHOSEN,
        "model_views": model_views,
        "backtest": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()} for s in summary],
        "backtest_detail": [{"origin": r["origin"], "target": r["target"], "rail": r["rail"],
                             "forecast": r1(r["forecast_mn"]), "actual": r1(r["actual_mn"]),
                             "error_pct": round(r["error_pct"], 1)}
                            for r in bt_rows if r["model"] == CHOSEN and r["design"] == "fixed"],
        "monthly_recent": {r: [[m, r1(v)] for m, v in zip(months[-24:], vol[r][-24:])] for r in RAILS},
        "monthly_forecast": {r: [[row["month"], r1(row["median_mn"]), r1(row["lo80_mn"]), r1(row["hi80_mn"])]
                                 for row in final["monthly"] if row["rail"] == r] for r in RAILS},
    }
    DOCS_DATA.write_text(
        "// Generated by analysis/forecast_model.py; do not edit by hand.\n"
        "// Volumes in millions of transactions; values in ₹ crore; tickets in ₹.\n"
        f"const FORECAST = {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))};\n",
        encoding="utf-8")

    best = min((s for s in summary if s["model"] != "debit_decay"), key=lambda s: s["mape_fixed"])
    print("Backtest MAPE (fixed origins, 5 rails x 3 targets) and rolling 12-month MAPE:")
    for s in summary:
        roll = "   n/a" if s["mape_rolling12"] is None else f"{s['mape_rolling12']:6.2f}"
        share = "n/a" if s["share_mae_pp"] is None else f"{s['share_mae_pp']:.2f} pp"
        rails = " ".join("  -  " if s[f"mape_fixed_{r}"] is None else f"{s[f'mape_fixed_{r}']:5.1f}"
                         for r in RAILS)
        print(f"  {s['model']:14s} {s['mape_fixed']:6.2f} {roll}  share MAE {share}  rails {rails}")
    print(f"Lowest fixed-origin MAPE: {best['model']}; chosen: {CHOSEN}")
    c = calibration
    print(f"kappa = {kappa:.3f} (rms12 {c['rms12']:.2f}, rms24 {c['rms24']:.2f}); 80% coverage "
          f"12m {c['coverage80_12m_raw']:.0f}% -> {c['coverage80_12m_calibrated']:.0f}% (n={c['n12']}), "
          f"2nd year {c['coverage80_24m_raw']:.0f}% -> {c['coverage80_24m_calibrated']:.0f}% (n={c['n24']})")
    print("  two-year check from Mar 2024:", {r: v["covered"] for r, v in c["two_year_from_2024_03"].items()})
    for row in annual:
        if row["fiscal_year"] == "FY2029-30":
            print(f"  FY2029-30 {row['rail']:12s} {row['volume_mn_median']:10.0f} mn "
                  f"[{row['volume_mn_lo80']:.0f}, {row['volume_mn_hi80']:.0f}]  share "
                  f"{row['share_pct_median']:.2f}% [{row['share_pct_lo80']:.2f}, {row['share_pct_hi80']:.2f}]")
    print("Latent ETS parameters:", final["params"])
    for view in model_views:
        print(f"  FY30 {view['model']:15s}", " ".join(f"{NAMES[r]} {view['fy30'][r]:8.0f}" for r in RAILS))
    print(f"Wrote {DOCS_DATA.relative_to(REPO)} and analysis/output/forecast_*.csv")


if __name__ == "__main__":
    main()
