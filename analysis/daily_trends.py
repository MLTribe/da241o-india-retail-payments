#!/usr/bin/env python3
"""Weekly and monthly trends for UPI, IMPS, NEFT, credit cards and debit cards.

Source: RBI "Payment and Settlement Systems - Daily Data Publication" workbook (one sheet per
month, June 2020 onward), archived in source_archive/rbi/. Volumes are published in lakh and
values in Rs crore. Card columns start in November 2020 (PoS + e-commerce combined) and are split
into "at PoS" and "at e-Commerce" from October 2021; both parts are summed here.

Rules:
  - Columns are located by their header labels, because RBI inserts columns over time.
  - "h" (holiday: the system did not settle that day) counts as zero; a blank cell is missing.
  - Only complete periods are kept: Monday-Sunday weeks and calendar months with every day present.
  - The comparison starts on 1 November 2020, the first day all five systems are published.
  - Shares are of the five systems together, not of all payments in India. UPI includes
    person-to-person transfers, NEFT includes business payments, and cards are purchases.

Long-run monthly view: the daily-based months are compared with the project's official monthly
series (NPCI product statistics for UPI and IMPS; RBI PSI release pages for NEFT; the spliced RBI
card series from long_series.py for cards) and appended after the official data end only where
the two sources agree in level. Card-network card payments are shown beside the official card
series, never joined to it. NEFT before June 2020 comes from RBI's bank-wise NEFT pages
(source_archive/rbi/neft_html/), checked against the PSI pages where both exist.

UPI, IMPS and NEFT from April 2016 (Analyses A-D): volume, value, shares of the three systems
combined (not of all payments in India) and average payment, by month and fiscal year.
Analysis E: OLS ln(UPI volume_t) = b0 + b1 ln(banks live on UPI_t) + b2 Trend_t + e_t, with
Newey-West standard errors, on the full sample and without the 2016-19 launch years.

Run after long_series.py: python3 analysis/daily_trends.py
Writes analysis/output/daily_five_systems.csv, weekly_five_systems.csv, monthly_five_systems.csv,
long_monthly_five_systems.csv, daily_vs_official_monthly.csv, three_systems_fiscal_years.csv,
upi_banks_regression.csv and docs/trends-data.js.
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKBOOK = REPO / "source_archive" / "rbi" / "rbi_payment_systems_daily_data_2020-06_2026-10.xlsx"
PSI_HTML = REPO / "source_archive" / "rbi" / "html"
YEAR_ROOT = REPO / "by_year"
OUTPUT = REPO / "analysis" / "output"
DOCS = REPO / "docs"
sys.path.insert(0, str(REPO / "scripts"))
from import_rbi_psi_pages import TableRows, normalise  # noqa: E402

LONG_START = "2014-06"
CARD_PSI_START = "2021-06"
JOIN_MAX_GAP_PCT = 1.0
NETWORK = " (card networks)"
NEFT_HTML = REPO / "source_archive" / "rbi" / "neft_html"
THREE = ["UPI", "IMPS", "NEFT"]
THREE_START = "2016-04"
REGRESSION_SAMPLES = [("Full sample", "2016-07"), ("Without the launch years", "2019-04")]

START = date(2020, 11, 1)
SYSTEMS = ["UPI", "IMPS", "NEFT", "Credit card", "Debit card"]
HEADER_TO_SYSTEM = {"UPI": "UPI", "IMPS": "IMPS", "NEFT": "NEFT",
                    "Credit Card": "Credit card", "Debit Card": "Debit card"}

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
EXCEL_EPOCH = datetime(1899, 12, 30)


def column_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_sheets(path: Path) -> list[tuple[str, list[list]]]:
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        sheets = []
        for sheet in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{NS}sheet"):
            target = rels[sheet.get(f"{REL_NS}id")]
            member = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
            rows = []
            for row in ET.fromstring(z.read(member)).iter(f"{NS}row"):
                values: dict[int, object] = {}
                for c in row.iter(f"{NS}c"):
                    kind, v = c.get("t"), c.find(f"{NS}v")
                    if kind == "s" and v is not None:
                        value = shared[int(v.text)]
                    elif kind == "inlineStr":
                        value = "".join(t.text or "" for t in c.iter(f"{NS}t"))
                    elif v is None or v.text is None:
                        continue
                    elif kind in ("str", "e"):
                        value = v.text
                    else:
                        value = float(v.text)
                    values[column_index(c.get("r"))] = value
                width = max(values) + 1 if values else 0
                rows.append([values.get(i) for i in range(width)])
            sheets.append((sheet.get("name").strip(), rows))
    return sheets


def as_date(cell) -> date | None:
    if isinstance(cell, float) and 40000 < cell < 60000:
        return (EXCEL_EPOCH + timedelta(days=cell)).date()
    if isinstance(cell, str):
        text = re.sub(r"\s+", " ", cell.strip().replace(",", ", ")).replace(" ,", ",")
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                pass
    return None


def column_map(rows: list[list]) -> tuple[int, dict[str, dict[str, list[int]]]]:
    """Return the first data row and, per system, the Vol and Val column indices."""
    name_row = next(i for i, r in enumerate(rows[:15]) if any(isinstance(c, str) and c.strip() == "RTGS" for c in r))
    measure_row = next(i for i in range(name_row + 1, name_row + 4)
                       if sum(1 for c in rows[i] if isinstance(c, str) and c.strip() in ("Vol", "Val")) >= 10)
    names, current = rows[name_row], None
    found: dict[str, dict[str, list[int]]] = {s: {"Vol": [], "Val": []} for s in SYSTEMS}
    for col, label in enumerate(rows[measure_row]):
        if col < len(names) and isinstance(names[col], str) and names[col].strip():
            current = names[col].strip()
        if not isinstance(label, str) or label.strip() not in ("Vol", "Val") or current is None:
            continue
        system = next((s for h, s in HEADER_TO_SYSTEM.items() if current == h or current.startswith(h + " ")), None)
        if system:
            found[system][label.strip()].append(col)
    return measure_row + 1, found


def number(cell) -> tuple[float | None, bool]:
    if isinstance(cell, float):
        return cell, False
    if isinstance(cell, str) and cell.strip().lower() == "h":
        return 0.0, True
    if isinstance(cell, str):
        try:
            return float(cell.replace(",", "")), False
        except ValueError:
            return None, False
    return None, False


def load_daily() -> tuple[dict[date, dict[str, dict[str, float]]], dict[str, int]]:
    daily: dict[date, dict[str, dict[str, float]]] = {}
    holidays = {s: 0 for s in SYSTEMS}
    for title, rows in read_sheets(WORKBOOK):
        first, cols = column_map(rows)
        for row in rows[first:]:
            day = as_date(row[0]) if row else None
            if day is None:
                continue
            record = {}
            for system in SYSTEMS:
                if not cols[system]["Vol"]:
                    continue
                vol = val = 0.0
                complete, holiday = True, False
                for measure, indices in cols[system].items():
                    for i in indices:
                        x, h = number(row[i] if i < len(row) else None)
                        holiday |= h
                        if x is None:
                            complete = False
                        elif measure == "Vol":
                            vol += x
                        else:
                            val += x
                if complete:
                    record[system] = {"vol_mn": vol / 10, "val_cr": val}
                    holidays[system] += holiday
            if day in daily:
                raise SystemExit(f"{day} appears twice (sheet {title})")
            daily[day] = record
    return daily, holidays


def aggregate(daily, key_of, length_of):
    groups: dict[str, list[date]] = {}
    for day in sorted(daily):
        if day >= START:
            groups.setdefault(key_of(day), []).append(day)
    periods = []
    for key, days in groups.items():
        if len(days) != length_of(days[0]):
            continue
        if not all(all(s in daily[d] for s in SYSTEMS) for d in days):
            continue
        totals = {s: {"vol_mn": sum(daily[d][s]["vol_mn"] for d in days),
                      "val_cr": sum(daily[d][s]["val_cr"] for d in days)} for s in SYSTEMS}
        periods.append((key, days[0], totals))
    return periods


def week_key(day: date) -> str:
    return (day - timedelta(days=day.weekday())).isoformat()


def month_length(day: date) -> int:
    nxt = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return (nxt - date(day.year, day.month, 1)).days


def series(periods, lag: int) -> dict:
    out = {"periods": [p[0] for p in periods], "systems": {}}
    vol_total = [sum(t[s]["vol_mn"] for s in SYSTEMS) for _, _, t in periods]
    val_total = [sum(t[s]["val_cr"] for s in SYSTEMS) for _, _, t in periods]
    for s in SYSTEMS:
        vol = [t[s]["vol_mn"] for _, _, t in periods]
        val = [t[s]["val_cr"] for _, _, t in periods]
        yoy = [None] * len(vol)
        for i in range(lag, len(vol)):
            if vol[i - lag] > 0:
                yoy[i] = round(100 * (vol[i] / vol[i - lag] - 1), 2)
        out["systems"][s] = {
            "volume": [round(v, 3) for v in vol],
            "value": [round(v, 1) for v in val],
            "volShare": [round(100 * v / t, 3) for v, t in zip(vol, vol_total)],
            "valShare": [round(100 * v / t, 3) for v, t in zip(val, val_total)],
            "ticket": [round(10 * b / a, 1) if a > 0 else None for a, b in zip(vol, val)],
            "yoy": yoy,
        }
    return out


def twelve_month_summary(monthly) -> list[dict]:
    if len(monthly) < 24:
        return []
    first, last = monthly[:12], monthly[-12:]
    years = (len(monthly) - 12) / 12

    def totals(block):
        vol = {s: sum(t[s]["vol_mn"] for _, _, t in block) for s in SYSTEMS}
        val = {s: sum(t[s]["val_cr"] for _, _, t in block) for s in SYSTEMS}
        return vol, val

    v0, a0 = totals(first)
    v1, a1 = totals(last)
    rows = []
    for s in SYSTEMS:
        rows.append({
            "system": s,
            "firstLabel": f"{first[0][0]} – {first[-1][0]}",
            "lastLabel": f"{last[0][0]} – {last[-1][0]}",
            "volFirst": round(v0[s], 1), "volLast": round(v1[s], 1),
            "volGrowthPa": round(100 * ((v1[s] / v0[s]) ** (1 / years) - 1), 1),
            "valGrowthPa": round(100 * ((a1[s] / a0[s]) ** (1 / years) - 1), 1),
            "volShareFirst": round(100 * v0[s] / sum(v0.values()), 2),
            "volShareLast": round(100 * v1[s] / sum(v1.values()), 2),
            "valShareFirst": round(100 * a0[s] / sum(a0.values()), 2),
            "valShareLast": round(100 * a1[s] / sum(a1.values()), 2),
            "ticketFirst": round(10 * a0[s] / v0[s]), "ticketLast": round(10 * a1[s] / v1[s]),
        })
    return rows


def weekday_profile(daily, weeks) -> dict:
    """Average volume by day of week over the last 52 complete weeks, Monday-Friday average = 100."""
    last = weeks[-52:]
    days = [w[1] + timedelta(days=k) for w in last for k in range(7)]
    profile = {}
    for s in SYSTEMS:
        by_dow = [[] for _ in range(7)]
        for d in days:
            by_dow[d.weekday()].append(daily[d][s]["vol_mn"])
        means = [sum(x) / len(x) for x in by_dow]
        base = sum(means[:5]) / 5
        profile[s] = [round(100 * m / base) for m in means]
    return {"from": last[0][0], "to": (last[-1][1] + timedelta(days=6)).isoformat(), "index": profile}


def shift_month(month: str, k: int) -> str:
    y, m = map(int, month.split("-"))
    n = y * 12 + m - 1 + k
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


def month_span(start: str, end: str) -> list[str]:
    out = [start]
    while out[-1] < end:
        out.append(shift_month(out[-1], 1))
    return out


def neft_psi() -> dict[str, tuple[float, float]]:
    """NEFT volume (mn) and value (Rs crore) from the archived RBI PSI release pages.

    Each page reports four columns per measure: fiscal year to date, the same month last year,
    the previous month and the release month. The release month is used; the same-month-last-year
    column fills the twelve months before the first archived release.
    """
    out: dict[str, tuple[float, float]] = {}
    for path in sorted(PSI_HTML.glob("20??-??_id*.html")):
        month = path.name[:7]
        parser = TableRows()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        row = next((r for r in ([normalise(c) for c in r] for r in parser.rows)
                    if r and re.fullmatch(r"\d+(\.\d+)*\s+NEFT", r[0])), None)
        if row is None or len(row) < 9:
            raise SystemExit(f"{path.name}: NEFT row not found")
        vol = [float(c.replace(",", "")) for c in row[1:5]]
        val = [float(c.replace(",", "")) for c in row[5:9]]
        out[month] = (vol[3] / 10, val[3])
        out.setdefault(shift_month(month, -12), (vol[1] / 10, val[1]))
    return out


def neft_bankwise() -> dict[str, tuple[float, float]]:
    """NEFT all-bank monthly totals from RBI's bank-wise ECS/NEFT/RTGS/Mobile pages (NEFTView.aspx).

    Totals are labelled "No. of transactions in mn and Amount in bn" up to July 2019 and
    "in lakh and Amount in crore" from August 2019; both are converted to million and Rs crore.
    """
    months = "January|February|March|April|May|June|July|August|September|October|November|December"
    names = months.split("|")
    out: dict[str, tuple[float, float]] = {}
    for path in sorted(NEFT_HTML.glob("id*.html")):
        html = path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " "))
        m = re.search(rf"(?:Mobile Banking|RTGS Inward and Outward|Transaction Details)[^.]{{0,40}}?({months}),?\s+(\d{{4}})", text)
        if not m:
            raise SystemExit(f"{path.name}: month not found")
        month = f"{m.group(2)}-{names.index(m.group(1)) + 1:02d}"
        parser = TableRows()
        parser.feed(html)
        row = next((r for r in ([normalise(c) for c in r] for r in parser.rows)
                    if any(re.search(r"total\s*\(no\.? of trans", c, re.I) for c in r)), None)
        if row is None:
            raise SystemExit(f"{path.name}: NEFT total not found")
        label = next(c for c in row if re.search(r"total", c, re.I))
        vol, val = (float(c.replace(",", "")) for c in [c for c in row if c and c != label][:2])
        if re.search(r"in mn", label) and re.search(r"in (Rs\. )?bn", label):
            out[month] = (vol, val * 100)
        elif re.search(r"in lakh", label) and re.search(r"crore", label):
            out[month] = (vol / 10, val)
        else:
            raise SystemExit(f"{path.name}: unknown units in {label!r}")
    return out


def official_monthly() -> tuple[dict[str, dict[str, tuple[float, float]]], dict]:
    official: dict[str, dict[str, tuple[float, float]]] = {}
    for system, stem in (("UPI", "npci_upi_product_statistics"), ("IMPS", "npci_imps_product_statistics")):
        official[system] = {}
        for path in sorted(YEAR_ROOT.glob(f"*/{stem}.csv")):
            for r in csv.DictReader(path.open(newline="")):
                if r.get("volume_mn") and r.get("value_crore"):
                    official[system][r["month"]] = (float(r["volume_mn"]), float(r["value_crore"]))
    psi, bankwise = neft_psi(), neft_bankwise()
    overlap = sorted(set(psi) & set(bankwise))
    gaps = [(100 * (bankwise[m][0] / psi[m][0] - 1), 100 * (bankwise[m][1] / psi[m][1] - 1)) for m in overlap]
    neft_check = {"from": overlap[0], "to": overlap[-1], "months": len(overlap),
                  "maxVolGapPct": round(max(abs(g[0]) for g in gaps), 3),
                  "maxValGapPct": round(max(abs(g[1]) for g in gaps), 3),
                  "bankwiseFrom": min(bankwise)}
    official["NEFT"] = {**{m: v for m, v in bankwise.items() if m not in psi}, **psi}
    official["Credit card"], official["Debit card"] = {}, {}
    for r in csv.DictReader((OUTPUT / "long_series_panel.csv").open(newline="")):
        for system, n, v in (("Credit card", "credit_n", "credit_v"), ("Debit card", "debit_n", "debit_v")):
            if r[n] and r[v]:
                official[system][r["month"]] = (float(r[n]), float(r[v]))
    return official, neft_check


def upi_banks() -> dict[str, int]:
    out = {}
    for path in sorted(YEAR_ROOT.glob("*/npci_upi_product_statistics.csv")):
        for r in csv.DictReader(path.open(newline="")):
            if r.get("banks_live"):
                out[r["month"]] = int(float(r["banks_live"]))
    return out


def three_block(long: dict) -> dict:
    """UPI, IMPS and NEFT from April 2016, with shares of the three systems combined."""
    lo = long["periods"].index(THREE_START)
    periods = long["periods"][lo:]
    raw = {s: {k: long["systems"][s][k][lo:] for k in ("volume", "value", "ticket", "yoy")} for s in THREE}
    out = {"periods": periods, "systems": {}, "appendFrom": {s: long["appendFrom"][s] for s in THREE if s in long["appendFrom"]}}
    for key, share in (("volume", "volShare"), ("value", "valShare")):
        totals = [None if any(raw[s][key][i] is None for s in THREE) else sum(raw[s][key][i] for s in THREE)
                  for i in range(len(periods))]
        for s in THREE:
            raw[s][share] = [round(100 * v / t, 3) if t else None for v, t in zip(raw[s][key], totals)]
    out["systems"] = raw
    return out


def three_fiscal_years(three: dict) -> list[dict]:
    """Analyses A-D by Indian fiscal year (April-March); only complete years are kept."""
    rows = []
    for i, p in enumerate(three["periods"]):
        if p[5:] != "04" or i + 12 > len(three["periods"]):
            continue
        idx = range(i, i + 12)
        vol = {s: sum(three["systems"][s]["volume"][k] or 0 for k in idx) for s in THREE}
        val = {s: sum(three["systems"][s]["value"][k] or 0 for k in idx) for s in THREE}
        if any(three["systems"][s]["volume"][k] is None for s in THREE for k in idx):
            continue
        year = int(p[:4])
        rows.append({
            "fy": f"{year}-{str(year + 1)[2:]}",
            "systems": {s: {
                "volume": round(vol[s], 1), "value": round(val[s]),
                "volShare": round(100 * vol[s] / sum(vol.values()), 2),
                "valShare": round(100 * val[s] / sum(val.values()), 2),
                "ticket": round(10 * val[s] / vol[s]) if vol[s] else None,
            } for s in THREE},
        })
    return rows


def solve(a: list[list[float]]) -> list[list[float]]:
    n = len(a)
    m = [row[:] + [float(i == j) for j in range(n)] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        piv = m[c][c]
        m[c] = [v / piv for v in m[c]]
        for r in range(n):
            if r != c:
                f = m[r][c]
                m[r] = [v - f * w for v, w in zip(m[r], m[c])]
    return [row[n:] for row in m]


def ols(y: list[float], x: list[list[float]]) -> dict:
    """OLS with classical and Newey-West (Bartlett kernel) standard errors."""
    n, k = len(y), len(x[0])
    xtx_inv = solve([[sum(r[i] * r[j] for r in x) for j in range(k)] for i in range(k)])
    xty = [sum(r[i] * v for r, v in zip(x, y)) for i in range(k)]
    beta = [sum(xtx_inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    e = [v - sum(b * c for b, c in zip(beta, r)) for v, r in zip(y, x)]
    sse = sum(v * v for v in e)
    ybar = sum(y) / n
    r2 = 1 - sse / sum((v - ybar) ** 2 for v in y)
    s2 = sse / (n - k)
    se = [math.sqrt(s2 * xtx_inv[i][i]) for i in range(k)]
    lags = int(4 * (n / 100) ** (2 / 9))
    meat = [[sum(x[t][i] * x[t][j] * e[t] ** 2 for t in range(n)) for j in range(k)] for i in range(k)]
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1)
        for i in range(k):
            for j in range(k):
                g = sum(e[t] * e[t - l] * (x[t][i] * x[t - l][j] + x[t - l][i] * x[t][j]) for t in range(l, n))
                meat[i][j] += w * g
    hac = [[sum(xtx_inv[i][a] * meat[a][b] * xtx_inv[b][j] for a in range(k) for b in range(k))
            for j in range(k)] for i in range(k)]
    se_nw = [math.sqrt(hac[i][i] * n / (n - k)) for i in range(k)]
    dw = sum((e[t] - e[t - 1]) ** 2 for t in range(1, n)) / sse
    return {"beta": beta, "se": se, "seNw": se_nw, "r2": r2, "n": n, "lags": lags, "dw": dw}


def upi_bank_regression(official: dict) -> list[dict]:
    """Analysis E: ln(UPI volume_t) = b0 + b1 ln(banks live on UPI_t) + b2 Trend_t + e_t."""
    banks = upi_banks()
    months = sorted(m for m in official["UPI"] if m in banks and official["UPI"][m][0] > 0)
    out = []
    for label, start in REGRESSION_SAMPLES:
        sample = [m for m in months if m >= start]
        y = [math.log(official["UPI"][m][0]) for m in sample]
        x = [[1.0, math.log(banks[m]), float(t)] for t, m in enumerate(sample, 1)]
        fit = ols(y, x)
        terms = []
        for name, b, s, snw in zip(("Intercept (β0)", "ln(banks live on UPI) (β1)", "Trend, per month (β2)"),
                                   fit["beta"], fit["se"], fit["seNw"]):
            t = b / snw
            terms.append({"term": name, "coef": round(b, 4), "se": round(s, 4), "seNw": round(snw, 4),
                          "t": round(t, 2), "p": round(math.erfc(abs(t) / math.sqrt(2)), 4)})
        fitted = [math.exp(sum(b * c for b, c in zip(fit["beta"], r))) for r in x]
        out.append({"sample": label, "from": sample[0], "to": sample[-1], "n": fit["n"],
                    "banksFrom": banks[sample[0]], "banksTo": banks[sample[-1]],
                    "r2": round(fit["r2"], 4), "dw": round(fit["dw"], 2), "lags": fit["lags"], "terms": terms,
                    "fitted": {m: round(v, 3) for m, v in zip(sample, fitted)}})
    return out


def upi_bank_points(official: dict) -> list[list]:
    banks = upi_banks()
    return [[m, banks[m], round(official["UPI"][m][0], 3)]
            for m in sorted(official["UPI"]) if m in banks and official["UPI"][m][0] > 0]


def pearson(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def compare_sources(monthly, official) -> tuple[list[dict], list[list]]:
    """Daily-based vs official monthly totals in the months both cover."""
    daily_m = {p[0]: p[2] for p in monthly}
    rows, detail = [], []
    for s in SYSTEMS:
        start = CARD_PSI_START if s in ("Credit card", "Debit card") else None
        months = sorted(m for m in daily_m if m in official[s] and (start is None or m >= start))
        d = [daily_m[m][s]["vol_mn"] for m in months]
        o = [official[s][m][0] for m in months]
        gaps = [100 * (x / y - 1) for x, y in zip(d, o)]
        pos = {m: i for i, m in enumerate(months)}
        g12 = [(math.log(d[i] / d[pos[shift_month(m, -12)]]), math.log(o[i] / o[pos[shift_month(m, -12)]]))
               for i, m in enumerate(months) if shift_month(m, -12) in pos]
        g1 = [(math.log(d[i] / d[pos[shift_month(m, -1)]]), math.log(o[i] / o[pos[shift_month(m, -1)]]))
              for i, m in enumerate(months) if shift_month(m, -1) in pos]
        r_level = pearson(d, o)
        r_yoy = pearson([x for x, _ in g12], [y for _, y in g12])
        r_mom = pearson([x for x, _ in g1], [y for _, y in g1])
        last3 = sum(gaps[-3:]) / 3
        if max(abs(g) for g in gaps[-12:]) <= JOIN_MAX_GAP_PCT:
            verdict = "Same data: daily months are appended after the official series ends"
        elif r_yoy is not None and r_yoy >= 0.8:
            verdict = "Same ups and downs, different level: shown side by side, not joined"
        else:
            verdict = "Different level and trend: shown side by side, not joined"
        rows.append({
            "system": s, "official": {"UPI": "NPCI monthly", "IMPS": "NPCI monthly", "NEFT": "RBI PSI monthly"}.get(s, "RBI PSI monthly"),
            "from": months[0], "to": months[-1], "months": len(months),
            "gapMean": round(sum(gaps) / len(gaps), 2), "gapLast3": round(last3, 2),
            "gapMin": round(min(gaps), 2), "gapMax": round(max(gaps), 2),
            "rLevel": round(r_level, 3) if r_level is not None else None,
            "rYoy": round(r_yoy, 3) if r_yoy is not None else None,
            "rMom": round(r_mom, 3) if r_mom is not None else None,
            "join": max(abs(g) for g in gaps[-12:]) <= JOIN_MAX_GAP_PCT,
            "verdict": verdict,
        })
        detail += [[m, s, round(x, 3), round(y, 3), round(g, 3)] for m, x, y, g in zip(months, d, o, gaps)]
    return rows, detail


def long_block(monthly, official, comparison) -> tuple[dict, list[list]]:
    daily_m = {p[0]: p[2] for p in monthly}
    months = month_span(LONG_START, monthly[-1][0])
    joins = {r["system"]: r["join"] for r in comparison}
    names = SYSTEMS + [f"{s}{NETWORK}" for s in ("Credit card", "Debit card")]
    block = {"periods": months, "systems": {}, "appendFrom": {}, "officialSource": {}}
    csv_rows = []
    for name in names:
        base = name.replace(NETWORK, "")
        vol, val, src = [], [], []
        last_official = max(official[base]) if name == base else None
        for m in months:
            if name != base:
                pick = (daily_m[m][base]["vol_mn"], daily_m[m][base]["val_cr"], "daily") if m in daily_m else None
            elif m in official[base]:
                pick = (*official[base][m], "official")
            elif joins[base] and m > last_official and m in daily_m:
                pick = (daily_m[m][base]["vol_mn"], daily_m[m][base]["val_cr"], "daily")
            else:
                pick = None
            vol.append(round(pick[0], 3) if pick else None)
            val.append(round(pick[1], 1) if pick else None)
            src.append(pick[2] if pick else None)
            if pick:
                csv_rows.append([m, name, round(pick[0], 3), round(pick[1], 1), pick[2]])
        yoy = [None] * len(vol)
        for i in range(12, len(vol)):
            if vol[i] is not None and vol[i - 12]:
                yoy[i] = round(100 * (vol[i] / vol[i - 12] - 1), 2)
        block["systems"][name] = {
            "volume": vol, "value": val,
            "ticket": [round(10 * b / a, 1) if a and b is not None else None for a, b in zip(vol, val)],
            "yoy": yoy,
        }
        if name == base and "daily" in src:
            block["appendFrom"][name] = months[src.index("daily")]
    return block, csv_rows


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def run() -> None:
    daily, holidays = load_daily()
    days = sorted(d for d in daily if d >= START)
    gaps = [d for a, b in zip(days, days[1:]) for d in [a] if (b - a).days != 1]
    if gaps:
        raise SystemExit(f"missing days after {gaps[:5]}")
    weeks = aggregate(daily, week_key, lambda d: 7)
    months = aggregate(daily, lambda d: d.strftime("%Y-%m"), month_length)

    OUTPUT.mkdir(exist_ok=True)
    write_csv(OUTPUT / "daily_five_systems.csv", ["date", "system", "volume_mn", "value_cr"],
              [[d.isoformat(), s, round(daily[d][s]["vol_mn"], 4), round(daily[d][s]["val_cr"], 2)]
               for d in days for s in SYSTEMS if s in daily[d]])
    for name, periods in (("weekly_five_systems.csv", weeks), ("monthly_five_systems.csv", months)):
        write_csv(OUTPUT / name, ["period_start", "system", "volume_mn", "value_cr"],
                  [[p[1].isoformat(), s, round(p[2][s]["vol_mn"], 3), round(p[2][s]["val_cr"], 1)]
                   for p in periods for s in SYSTEMS])

    official, neft_check = official_monthly()
    comparison, detail = compare_sources(months, official)
    long, long_rows = long_block(months, official, comparison)
    three = three_block(long)
    fiscal = three_fiscal_years(three)
    regression = upi_bank_regression(official)
    write_csv(OUTPUT / "three_systems_fiscal_years.csv",
              ["fiscal_year", "system", "volume_mn", "value_cr", "volume_share_of_three_pct",
               "value_share_of_three_pct", "average_value_rs"],
              [[r["fy"], s, *r["systems"][s].values()] for r in fiscal for s in THREE])
    write_csv(OUTPUT / "upi_banks_regression.csv",
              ["sample", "from", "to", "n", "term", "coef", "se_ols", "se_newey_west", "t_nw", "p_nw", "r2", "durbin_watson"],
              [[r["sample"], r["from"], r["to"], r["n"], t["term"], t["coef"], t["se"], t["seNw"], t["t"], t["p"], r["r2"], r["dw"]]
               for r in regression for t in r["terms"]])
    write_csv(OUTPUT / "daily_vs_official_monthly.csv",
              ["month", "system", "daily_based_volume_mn", "official_volume_mn", "gap_pct"], detail)
    write_csv(OUTPUT / "long_monthly_five_systems.csv",
              ["month", "series", "volume_mn", "value_cr", "source"], long_rows)

    payload = {
        "source": "RBI, Payment and Settlement Systems – Daily Data Publication (updated to "
                  f"{max(daily).strftime('%-d %B %Y')})",
        "systems": SYSTEMS,
        "firstDay": days[0].isoformat(), "lastDay": days[-1].isoformat(),
        "weekly": series(weeks, 52),
        "monthly": series(months, 12),
        "long": long,
        "three": three,
        "fiscal": fiscal,
        "regression": regression,
        "upiBanks": upi_bank_points(official),
        "neftCheck": neft_check,
        "comparison": comparison,
        "summary": twelve_month_summary(months),
        "weekday": weekday_profile(daily, weeks),
        "holidayDays": holidays,
    }
    js = "window.TRENDS_DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    (DOCS / "trends-data.js").write_text(js, encoding="utf-8")
    print(f"days {days[0]} – {days[-1]}; weeks {len(weeks)} ({weeks[0][0]} – {weeks[-1][0]}); "
          f"months {len(months)} ({months[0][0]} – {months[-1][0]}); holiday days {holidays}")
    for r in comparison:
        print(f"  {r['system']:12s} {r['from']}..{r['to']} n={r['months']:2d} gap mean {r['gapMean']:+6.2f}% "
              f"[{r['gapMin']:+.2f}, {r['gapMax']:+.2f}] last3 {r['gapLast3']:+.2f}%  r(level) {r['rLevel']}  "
              f"r(yoy) {r['rYoy']}  r(mom) {r['rMom']}  join={r['join']}")
    print(f"  appended from: {long['appendFrom']}")
    print(f"  NEFT bank-wise vs PSI: {neft_check}")
    for r in fiscal:
        print("  FY", r["fy"], {s: (r["systems"][s]["volShare"], r["systems"][s]["valShare"], r["systems"][s]["ticket"]) for s in THREE})
    for r in regression:
        print(f"  E {r['sample']} {r['from']}..{r['to']} n={r['n']} R2={r['r2']} DW={r['dw']} lags={r['lags']}")
        for t in r["terms"]:
            print(f"     {t['term']:28s} {t['coef']:+.4f}  se {t['se']:.4f}  NW se {t['seNw']:.4f}  t {t['t']:+.2f}  p {t['p']}")


if __name__ == "__main__":
    run()
