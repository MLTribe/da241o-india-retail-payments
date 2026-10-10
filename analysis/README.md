# Payment-mix model for Assignment 2

## Question and choice of model

The submitted research question asks how the **absolute levels and relative
shares** of IMPS, domestic debit-card purchases, and domestic credit-card
purchases changed alongside UPI's expansion in India. The model is a
**fixed-basket, monthly comparative accounting model**. It separately tracks:

1. Total UPI and IMPS, April 2016–March 2026 (120 months).
2. UPI person-to-merchant (P2M), domestic credit-card purchases, and domestic
   debit-card purchases, June 2021–March 2026 (58 months): UPI's mature
   phase, the longest period with consistently classified RBI card data.

A secondary, spliced card series back to June 2014 tests the 2017–20
expansion phase as a robustness check (see "Long series and structural
breaks" below).

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
multiplies nominal value and ticket by `March 2026 CPI / month CPI`. The CPI
back-series starts in 2014, so real measures cover every month of both
baskets. Fiscal-year totals run April to
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
| `analysis/output/infrastructure_panel.csv` | Monthly acceptance points, cards in force, and use per point or card from RBI PSI Part III, June 2021–March 2026 |
| `analysis/output/figures/*.svg` | Static charts for the paper, drawn without a plotting package |
| `docs/explore-data.js` | Data file for the interactive `docs/explore.html` page |

The merchant window starts at the first month in which NPCI P2M, RBI cards and
MoSPI CPI are all observed (currently June 2021, set by RBI's card
reclassification; NPCI P2M starts April 2020 and CPI in 2014). The first full
fiscal year in this window is still FY2022-23.

The infrastructure panel uses 58 own-month RBI PSI Part III release-page
observations: January 2022 onward from `scripts/import_rbi_psi_pages.py`,
June–December 2021 from `scripts/import_card_history.py`. Its monthly
debit-card payment intensity is domestic debit-card purchases divided by
month-end debit cards in force. Cards in force count instruments, not distinct
people or active users, so the ratio cannot identify who kept a card or what
payment method they used instead. Other RBI payment rails still require the
monthly workbooks.

The program stops if a required month is absent, a month appears twice, units
are invalid, or the within-source component totals fail beyond published
rounding. It does not fill missing observations. Its results are deterministic
from the checked-in CSVs and need only Python's standard library.

## Hypothesis register

```shell
python3 analysis/hypothesis_tests.py
```

This tests each hypothesis in `REGISTER` against the same CSVs and writes
`analysis/output/hypotheses.csv` and `docs/hypotheses-data.js`, which feeds
`docs/hypotheses.html`. Most tests are drift tests: the mean 12-month log
change of a series, with Newey-West (Bartlett, 12-lag) standard errors. A
hypothesis is Supported when the 95% interval lies entirely on the predicted
side of zero, Contradicted when it lies entirely on the other side, and Not
supported otherwise.

To add a hypothesis, write a function that returns `drift_hypothesis(...)`
(or `hypothesis(...)` for a custom test, or `pending(...)` when the data are
not yet available), append it to `REGISTER`, and rerun the script. The page
picks it up without HTML changes. Fix the direction and metric before
looking at the result. Give the tested series as LaTeX in `formula=` (symbol
definitions in `where=`); the page renders it with KaTeX alongside the
generated hypothesis and decision rule. Custom tests pass
`formulas=[(label, latex), ...]`.

Each card also shows "How the verdict is reached": one step per test, with
the plain question, the estimate and interval filled in with this data's
numbers, the check, and the step's verdict, then the rule that combines the
steps into the final verdict. Drift tests build this automatically (pass a
plain-language `question=`); custom tests pass
`derivation=derivation([drift_step(...), ...], final_verdict)`.

**Sample periods.** Window-dependent tests use `MERCHANT_START` (June 2021).
Its first base month falls in the COVID second wave, so the whole register is
re-run from `ROBUSTNESS_START` (January 2022, the earlier draft window) and
written to `analysis/output/hypotheses_robustness.csv`. Two marginal verdicts
differ: H4 (volume vs value share gain) is Supported from June 2021 but not
from January 2022, and S3 (real credit ticket falls) the reverse. Every other
verdict is unchanged.

**Issuers, acquirers and merchants (objective O4).** A1 (UPI QR vs Bharat QR),
A2 (PoS terminal stock), I1 (credit vs debit cards in force), I2 (credit share
of card value) and M1 (credit share of merchant-basket value, a lower bound on
the fee-bearing share) test observable footprints of the managerial
arguments. The mechanisms in their "why we expect it" text are labelled
speculative: national totals do not show firm-level costs or strategies.

## Long series and structural breaks (secondary)

```shell
python3 analysis/long_series.py   # CSV outputs only; hypothesis_tests.py also runs it
```

`long_series.py` splices card payments back to June 2014 and tests UPI's
expansion phase. Bridging assumptions:

1. From June 2021 card payments are RBI PSI PoS + Others.
2. Before June 2021 they are the all-bank Total row of RBI's bank-wise
   ATM/PoS/card pages. In the pre-March-2022 layouts the "PoS" column covers
   every card payment, online included: it equals PSI PoS + Others in the
   overlap (Jan 2022 credit 195.81 mn in both sources).
3. The bank-wise series is scaled by `k`, the geometric-mean PSI ÷ bank-wise
   ratio over June 2021–February 2022, separately for debit/credit and
   volume/value. `k` is 1.000 for credit and 1.004 (volume) / 1.009 (value)
   for debit.
4. `k` is checked out of sample against PSI's "same month last year" values
   for June 2020–May 2021: mean absolute error 0.03–1.0%, maximum 1.9%.
5. RBI's June 2021 reclassification changes presentation, not the card total.

Bank-wise values are published in ₹ million up to June 2019 and ₹ lakh from
then on; pages without a unit label are scaled by their neighbours, and the
average ticket is continuous across the switch. Before April 2020 NPCI does
not split UPI into P2P and P2M, so expansion-phase comparisons use total UPI,
an upper bound on merchant use.

Expansion-phase hypotheses E1–E4 average year-on-year changes from April 2018
to February 2020 (levels April 2017 onward, ending before COVID); starting in
April 2018 keeps demonetisation out of every base month.

| Break | Date | Handling |
| --- | --- | --- |
| Demonetisation | Nov 2016 | Kept out of every window and base month |
| NPCI same-account exclusion | Aug 2018 | Level-shift test, ±12 months, quadratic trend, IMPS placebo; E2 also reported without straddling months |
| Zero MDR on UPI and RuPay debit | Jan 2020 | Level + trend break, Apr 2017–Mar 2023, month and COVID dummies; debit ÷ credit nets common shocks |
| COVID-19 | Mar–Dec 2020 (wave 2 Apr–Jun 2021) | One dummy per month in break models; expansion tests end Feb 2020; robustness start Jan 2022 |
| NPCI P2P/P2M split begins | Apr 2020 | Sample boundary for P2M |
| RBI card reclassification | Jun 2021 | Splice point; overlap check and level dummy |

Only the June 2021 splice point and the COVID second wave touch the primary
sample (through its first base month); the other breaks fall before it.
Outputs: `analysis/output/long_series_panel.csv` (spliced monthly panel with
both sources) and `analysis/output/structural_breaks.csv`. The break tests
are tests of a change in path at a known date, not causal policy effects.

## Landscape hypotheses (banks, UPI apps, states)

```shell
python3 analysis/landscape_tests.py
```

This uses the bank-wise, app-wise and state-wise CSVs described in the
top-level README and writes `analysis/output/landscape_hypotheses.csv` and
`docs/landscape-data.js` for `docs/landscape.html`. It reuses the drift test,
verdict rules and derivation helpers from `hypothesis_tests.py`, and adds:

- exact one-sided binomial sign tests across banks or states;
- cross-section OLS with HC1 standard errors (one observation per bank or
  state, annualised change from FY2022-23 to FY2025-26 or 2022 to 2025);
- quarterly drift tests (4-quarter changes, 4 Newey-West lags) for PhonePe
  Pulse;
- a known-date level break at the Paytm Payments Bank order (Feb 2024).

Shares are tested on the log-odds scale. The page also shows descriptive
distributions (largest issuers, app shares and HHI, state shares and NPCI's
unclassified share). Notes on each card record judgement calls: the
Citibank-to-Axis transfer in B3, and the 2018-start robustness results for R1
and R2.

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
