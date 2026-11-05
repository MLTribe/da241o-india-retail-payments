# Final analysis page data

Every table and chart on the Final analysis page (`docs/final.html`), one CSV each, in page order. The numbers are exactly what the page shows; they are exported from `docs/final-data.js`.

Window: 2017-11 to 2026-03 (101 months; 72 for UPI P2M, from 2020-04). RBI daily data extend UPI and IMPS to 2026-09.

Charts are in wide format: one row per month, one column per line. Blank cells are months a series does not cover. Nested results (H2 and H3 summaries) are listed as `item, value` pairs.

| File | Page section | Rows |
| --- | --- | --- |
| `01_answer_headline.csv` | The answer: headline paragraph | 1 |
| `01b_answer_verdicts.csv` | The answer: H1–H3 verdict cards | 3 |
| `01c_answer_summary.csv` | The answer: label per payment system (primary method) | 3 |
| `01d_answer_robustness_checks.csv` | The answer: agreement across robustness variants | 15 |
| `02_corrections.csv` | What changed from Assignment 2 | 6 |
| `03_fig1_index_nov2017.csv` | Fig 1. Transactions per month, Nov 2017 = 100 | 101 |
| `03b_fig1b_index_apr2020.csv` | Fig 1b. UPI P2M against cards, Apr 2020 = 100 | 72 |
| `03c_fig2_shares.csv` | Fig 2. Shares within comparison groups (%) | 101 |
| `03d_fig3_real_tickets.csv` | Fig 3. Average ticket, ₹ at Mar 2026 prices | 101 |
| `03e_chart_markers.csv` | Shaded COVID waves and dashed event lines on all charts | 7 |
| `04_pre_post_covid.csv` | Growth and share by period | 19 |
| `05_classification.csv` | Classification: every method and variant | 14 |
| `05b_classification_heatmap.csv` | Heat-map: threshold × measure × window | 27 |
| `05c_fig4_drawdown.csv` | Fig 4. Drawdown of the 12-month average from its running peak (%) | 101 |
| `06_share_decomposition.csv` | Own growth against comparison-group growth | 7 |
| `07_h1.csv` | H1: IMPS and debit tickets | 2 |
| `07b_h2_breaks.csv` | H2: break search per series | 6 |
| `07c_h2_summary.csv` | H2: timing counts and the debit ÷ credit ratio test | 21 |
| `07d_h3_summary.csv` | H3: trends, slope break, P2M log-odds and card supply | 31 |
| `07e_h3_decomposition.csv` | H3: change in credit's card share split into credit and debit parts | 3 |
| `08_magnitudes.csv` | What the shifts mean in transactions and rupees | 24 |
| `09_sensitivity.csv` | Sensitivity checks | 6 |
| `09b_card_bridge_summary.csv` | Card bridge: bank-wise against RBI PSI | 4 |
| `09c_card_bridge_factors.csv` | Card bridge: scale factors and their test | 4 |
| `10_diagnostics.csv` | Unit roots and stationarity (ADF, KPSS) | 19 |
| `10b_trend_tests.csv` | Trend estimates | 84 |

The input data behind these numbers are in `../final_analysis_data/`.

Rebuild from the `assignment2` folder:

```shell
python3 analysis/final_analysis.py           # refreshes docs/final-data.js
python3 scripts/export_final_page_data.py    # rewrites this folder
```
