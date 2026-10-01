# Payment-mix model for Assignment 2

## Question and choice of model

The submitted research question asks how the **absolute levels and relative
shares** of IMPS, domestic debit-card purchases, and domestic credit-card
purchases changed alongside UPI's expansion in India. The model is a
**fixed-basket, monthly comparative accounting model**. It separately tracks:

1. Total UPI and IMPS, April 2016–March 2026 (120 months).
2. UPI person-to-merchant (P2M), domestic credit-card purchases, and domestic
   debit-card purchases, January 2022–March 2026 (51 months).

This is the best primary method for the available data because the question is
about observed levels and shares. The inputs are national monthly aggregates,
with no record of a customer's payment choice or a merchant's acceptance
decision. A regression of one trending rail on another could mistake common
growth for substitution. The model answers the stated question directly and
keeps the two different use cases in separate comparison groups.

## How the model works

For each month `t` and declared basket `b`, let `N[r,t]` be a rail's transaction
volume in **millions**, and let `V[r,t]` be its nominal transaction value in
**₹ crore**. The model computes:

```text
B[b,t]             = sum of N[r,t] over rails in basket b
volume_share[r,t] = N[r,t] / B[b,t]
value_share[r,t]  = V[r,t] / sum of V[k,t] over rails in basket b
avg_ticket[r,t]   = V[r,t] × 10 / N[r,t] rupees per transaction
volume_yoy[r,t]   = N[r,t] / N[r,t-12] - 1
share_yoy[r,t]    = volume_share[r,t] - volume_share[r,t-12]
```

The key accounting identity is
`12-month log change in share = 12-month log change in rail volume −
12-month log change in basket volume` when all terms are positive. Thus a rail
can gain transactions while losing share if the comparison group grows faster.
The classifications use the signs of each rail's year-on-year volume change and
share change:

| Volume | Share | Label |
| --- | --- | --- |
| Falls | Falls | Absolute contraction |
| Rises | Falls | Relative share loss |
| Rises | Rises | Co-expansion |
| Falls | Rises | Rail fell, share rose |

Changes within 0.005 percentage points of zero are labelled approximately
flat, reflecting source rounding. The first 12 months have no year-on-year
classification. A zero-volume prelaunch UPI month has no average ticket or
percentage growth from that zero base.

RBI card volume arrives in **lakh** transactions, so the script divides it by
10 before comparison with NPCI's millions. The model applies MoSPI's published
0.5267 link factor to put the old CPI observations on the 2024 base, then
multiplies nominal value and ticket by `March 2026 CPI / month CPI`. Real
measures are available only from January 2022. Fiscal-year totals run April to
March and include only complete 12-month years. Year-on-year comparisons use
the same calendar month to reduce ordinary seasonality.

## Run and inspect

From this repository's root:

```shell
python3 analysis/payment_mix_model.py
```

The script reads `by_year/YYYY/*.csv` and writes:

| Output | Purpose |
| --- | --- |
| `analysis/output/findings.md` | Plain-language results and key tables |
| `analysis/output/monthly_panel.csv` | Month-by-rail levels, shares, tickets, real values, and year-on-year labels |
| `analysis/output/annual_summary.csv` | Complete fiscal-year totals and shares |
| `analysis/output/period_comparison.csv` | First-to-last and most recent fiscal-year comparisons |
| `analysis/output/source_checks.json` | Coverage, published-identity checks, and NPCI table mismatches |

The program stops if a required month is absent, a month appears twice, units
are invalid, or the within-source component totals fail beyond published
rounding. It does not fill missing observations. Its results are deterministic
from the checked-in CSVs and need only Python's standard library.

## Reading the result

The main comparison is **growth versus displacement**. For FY2022-23 to
FY2025-26, UPI P2M volume rose 257.5%, credit-card volume rose 106.5% but
lost 2.19 percentage points of this basket, and debit-card volume fell 62.6%
while losing 6.19 points. Credit cards therefore show relative share loss over
this period, while debit cards show absolute contraction. Over the most recent
full year, IMPS volume also contracted even though its inflation-adjusted
transaction value rose. The exact computed figures and source checks are in
`output/findings.md`.

These are **analytical activity shares** among the specified methods, not
shares of every retail payment in India. Total UPI includes P2M, so the two
baskets cannot be added. PPI is not included in either primary basket because
its aggregate is not classified by the same merchant purpose. A lower P2M
average ticket is consistent with a different mix of transactions but does not
prove that card payments were replaced. The data cannot identify a causal
network effect, substitution elasticity, or the effect of any announced 2026
merchant-side charge.

The `source_manifest.csv` and `by_year/README.md` document provenance. The
P2P/P2M series was transcribed from NPCI's official monthly selector table;
the RBI card series was transcribed from each own-month release page. The script
flags April and July 2023 differences between NPCI's product-total and
ecosystem-total UPI tables rather than treating them as interchangeable.
