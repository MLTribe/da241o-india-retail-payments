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
| `analysis/output/source_checks.json` | Coverage, extended-basket windows, optional-series counts, published-identity checks, and NPCI table mismatches |
| `analysis/output/timeseries_stats.csv` | Trend growth, first-to-last 12-month growth, seasonal indices, and monthly HHI (long format) |
| `analysis/output/infrastructure_panel.csv` | Acceptance points, cards in force, and use per point or card (header only until RBI workbooks are imported) |
| `analysis/output/figures/*.svg` | Static charts for the paper, drawn without a plotting package |
| `docs/explore-data.js` | Data file for the interactive `docs/explore.html` page |

The merchant window starts at the first month in which NPCI P2M, RBI cards and
MoSPI CPI are all observed (currently January 2022). Adding earlier months to
all three series extends it automatically.

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

## Additional time series

The core baskets above answer the research question. The model also builds
extension and context baskets with the same share and pattern definitions.
Each basket is its own denominator, so shares are not comparable across baskets.

| Basket | Rails | Role |
| --- | --- | --- |
| `merchant_channel` | UPI P2M; credit PoS; credit Others; debit PoS; debit Others | Extension: is card contraction concentrated in the in-store channel? |
| `upi_use` | UPI P2P; UPI P2M | Extension: composition within UPI |
| `context_retail` | UPI total; IMPS; cards; PPI; plus NEFT, AePS fund transfers, NETC once RBI workbooks are imported | Context for scale only |
| `cash_context` | ATM cash withdrawals; UPI P2M | Context, needs RBI workbooks |
| `wholesale_context` | RTGS | Context, levels only, needs RBI workbooks |

An optional rail joins a basket only if it covers the basket's whole window, so
basket composition never changes from month to month. RBI's "Others" card
channel is mostly, but not only, online card-not-present purchases.

Time-series measures, all computed with the standard library:

```text
rolling12_volume[r,t]        = sum of N[r,t-11..t]
rolling12_volume_share[r,t]  = rolling12_volume[r,t] / sum over basket rails
trend growth (% per year)    = 100 × (exp(12 × b) − 1), where b is the OLS slope
                               of ln N[r,t] on the month index
first-to-last 12m growth     = (last-12 total / first-12 total)^(1 / years apart) − 1
seasonal index[m]            = mean over years of N[r,t] / centred 2×12 MA,
                               for calendar month m, rescaled so 12 months average 100
HHI[b,t]                     = sum over rails of volume_share[r,t]^2  (0–10,000)
intensity                    = monthly transactions / points or cards in force
```

Trend slopes are reported for the full window and the last 24 months. The
transfer basket is also split at April 2020, the first COVID-19 lockdown. That
split is a descriptive comparison, not an estimated structural break. Seasonal
indices need at least 36 positive months, and UPI's pre-launch zero months are
dropped. HHI measures concentration within the analytical basket only, not market
power. Intensity ratios measure deployed terminals, QR codes and cards, not
active merchants or customers. None of these measures identifies demand,
substitution, or a network effect.

The `source_manifest.csv` and `by_year/README.md` document provenance. The
P2P/P2M series was transcribed from NPCI's official monthly selector table;
the RBI card series was transcribed from each own-month release page. The script
flags April and July 2023 differences between NPCI's product-total and
ecosystem-total UPI tables rather than treating them as interchangeable.
