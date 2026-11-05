"""Export everything shown on docs/final.html to final_page_data/, one CSV per table or chart.

Reads docs/final-data.js (the file the page loads), so the CSVs hold exactly the numbers on
the page, in page order. Run analysis/final_analysis.py first to refresh that file.

Run from the assignment folder:  python3 scripts/export_final_page_data.py
"""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "docs" / "final-data.js"
DEST = REPO / "final_page_data"


def load() -> dict:
    text = SOURCE.read_text(encoding="utf-8")
    return json.loads(text[text.index("{"): text.rindex("}") + 1])


def flat(obj, prefix=""):
    """Nested dicts become dotted keys; lists of scalars are joined with '; '."""
    out = {}
    for key, value in obj.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flat(value, f"{name}."))
        elif isinstance(value, list):
            out[name] = "; ".join(str(v) for v in value)
        else:
            out[name] = value
    return out


def table(rows, drop=()):
    rows = [flat({k: v for k, v in row.items() if k not in drop}) for row in rows]
    fields = []
    for row in rows:
        fields += [k for k in row if k not in fields]
    return fields, rows


def wide(months, lines):
    fields = ["month"] + [line["name"] for line in lines]
    rows = [{"month": m, **{line["name"]: line["values"][i] for line in lines}} for i, m in enumerate(months)]
    return fields, rows


def key_values(obj, section):
    return ["section", "item", "value"], [{"section": section, "item": k, "value": v} for k, v in flat(obj).items()]


def main() -> None:
    D = load()
    S = D["series"]
    files = []  # (file name, page section, fields, rows)

    def add(name, section, fields_rows):
        files.append((name, section, *fields_rows))

    add("01_answer_headline.csv", "The answer: headline paragraph",
        (["headline"], [{"headline": D["headline"]}]))
    add("01b_answer_verdicts.csv", "The answer: H1–H3 verdict cards",
        (["hypothesis", "assignment2_rule", "v2_test"],
         [{"hypothesis": h, "assignment2_rule": v["a2"], "v2_test": v["v2"]} for h, v in D["verdicts"].items()]))
    add("01c_answer_summary.csv", "The answer: label per payment system (primary method)",
        table(D["summary"], drop=("checks",)))
    add("01d_answer_robustness_checks.csv", "The answer: agreement across robustness variants",
        (["method", "variant", "label", "agrees"],
         [{"method": s["method"], **c} for s in D["summary"] for c in s["checks"]]))
    add("02_corrections.csv", "What changed from Assignment 2", table(D["corrections"]))
    add("03_fig1_index_nov2017.csv", "Fig 1. Transactions per month, Nov 2017 = 100", wide(S["months"], S["index"]))
    add("03b_fig1b_index_apr2020.csv", "Fig 1b. UPI P2M against cards, Apr 2020 = 100",
        wide(S["p2mMonths"], S["indexP2m"]))
    add("03c_fig2_shares.csv", "Fig 2. Shares within comparison groups (%)", wide(S["months"], S["shares"]))
    add("03d_fig3_real_tickets.csv", "Fig 3. Average ticket, ₹ at Mar 2026 prices", wide(S["months"], S["tickets"]))
    add("03e_chart_markers.csv", "Shaded COVID waves and dashed event lines on all charts",
        (["type", "from", "to", "id", "label"],
         [{"type": "shaded band", "from": b["from"], "to": b["to"], "id": "", "label": b["label"]} for b in S["bands"]]
         + [{"type": "dashed line", "from": l["month"], "to": "", "id": l["id"], "label": l["label"]} for l in S["lines"]]))
    covid = []
    for row in D["covid"]:
        base = {"series": row["series"], "group": row["group"]}
        for period, values in row["periods"].items():
            covid.append({**base, "period": period, **flat(values or {})})
        if "diff" in row:
            covid.append({**base, "period": "Post minus pre (log points a year)", "est": row["diff"],
                          "se": row["diffSe"], "lo": row["diffLo"], "hi": row["diffHi"]})
    add("04_pre_post_covid.csv", "Growth and share by period", table(covid))
    add("05_classification.csv", "Classification: every method and variant", table(D["classification"]))
    add("05b_classification_heatmap.csv", "Heat-map: threshold × measure × window", table(D["grid"]))
    add("05c_fig4_drawdown.csv", "Fig 4. Drawdown of the 12-month average from its running peak (%)",
        wide(S["months"], S["drawdown"]))
    add("06_share_decomposition.csv", "Own growth against comparison-group growth", table(D["decomposition"]))
    add("07_h1.csv", "H1: IMPS and debit tickets", table(D["h1"]["rows"]))
    add("07b_h2_breaks.csv", "H2: break search per series", table(D["h2"]["rows"]))
    add("07c_h2_summary.csv", "H2: timing counts and the debit ÷ credit ratio test",
        key_values({k: v for k, v in D["h2"].items() if k != "rows"}, "H2"))
    add("07d_h3_summary.csv", "H3: trends, slope break, P2M log-odds and card supply",
        key_values({k: v for k, v in D["h3"].items() if k != "decomposition"}, "H3"))
    add("07e_h3_decomposition.csv", "H3: change in credit's card share split into credit and debit parts",
        table(D["h3"]["decomposition"]))
    add("08_magnitudes.csv", "What the shifts mean in transactions and rupees", table(D["magnitudes"]))
    add("09_sensitivity.csv", "Sensitivity checks", table(D["sensitivity"]))
    add("09b_card_bridge_summary.csv", "Card bridge: bank-wise against RBI PSI", table(D["bridge"]["summary"]))
    add("09c_card_bridge_factors.csv", "Card bridge: scale factors and their test", table(D["bridge"]["factors"]))
    add("10_diagnostics.csv", "Unit roots and stationarity (ADF, KPSS)", table(D["diagnostics"]))
    add("10b_trend_tests.csv", "Trend estimates", table(D["trend"]))

    shutil.rmtree(DEST, ignore_errors=True)
    DEST.mkdir()
    for name, _, fields, rows in files:
        with (DEST / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    window = D["window"]
    lines = [
        "# Final analysis page data",
        "",
        "Every table and chart on the Final analysis page (`docs/final.html`), one CSV each, in page order. "
        "The numbers are exactly what the page shows; they are exported from `docs/final-data.js`.",
        "",
        f"Window: {window['start']} to {window['end']} ({window['months']} months; {window['p2mMonths']} for UPI P2M, "
        f"from {window['p2mStart']}). RBI daily data extend UPI and IMPS to {window['dailyEnd']}.",
        "",
        "Charts are in wide format: one row per month, one column per line. Blank cells are months a series "
        "does not cover. Nested results (H2 and H3 summaries) are listed as `item, value` pairs.",
        "",
        "| File | Page section | Rows |",
        "| --- | --- | --- |",
        *[f"| `{name}` | {section} | {len(rows)} |" for name, section, _, rows in files],
        "",
        "The input data behind these numbers are in `../final_analysis_data/`.",
        "",
        "Rebuild from the `assignment2` folder:",
        "",
        "```shell",
        "python3 analysis/final_analysis.py           # refreshes docs/final-data.js",
        "python3 scripts/export_final_page_data.py    # rewrites this folder",
        "```",
        "",
    ]
    (DEST / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(files)} CSV files and README.md to {DEST.relative_to(REPO)}/")


if __name__ == "__main__":
    main()
