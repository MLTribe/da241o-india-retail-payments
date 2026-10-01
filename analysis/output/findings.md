# What the payment-mix model says

Data freeze: March 2026. Figures below are computed from the annual CSV extracts.
Volumes are transactions, values are rupees, and shares are within the named
analytical comparison basket. These shares are not economy-wide market shares.

## Merchant payments: FY2022-23 to FY2025-26

| Rail | FY22-23 volume (bn) | FY25-26 volume (bn) | Volume change | Share change | Pattern |
| --- | ---: | ---: | ---: | ---: | --- |
| UPI P2M | 42.60 | 152.31 | +257.5% | +8.37 pp | co-expansion |
| credit card | 2.92 | 6.02 | +106.5% | -2.19 pp | relative share loss |
| debit card | 3.42 | 1.28 | -62.6% | -6.19 pp | absolute contraction |

The fixed merchant basket includes UPI P2M, domestic credit-card purchases,
and domestic debit-card purchases. RBI volume (lakh) is divided by 10 to match
NPCI volume (millions). A falling share alongside rising transactions is
relative share loss; falling transactions and share is absolute contraction.

### Value and ticket size over the same fiscal years

| Rail | Real value change | Nominal value-share change | Real ticket change |
| --- | ---: | ---: | ---: |
| UPI P2M | +163.2% | +17.60 pp | -26.4% |
| credit card | +46.6% | -7.56 pp | -29.0% |
| debit card | -45.1% | -10.05 pp | +46.7% |

Real value and ticket use MoSPI CPI linked to the 2024 base and are
expressed in March 2026 rupees. Value shares use nominal values within
the same month or fiscal year.

### March 2026 transaction size

| Rail | Transactions (bn) | Nominal value (₹ lakh crore) | Average ticket (₹) | Volume share | Value share |
| --- | ---: | ---: | ---: | ---: | ---: |
| UPI P2M | 14.180 | 8.61 | 608 | 95.43% | 76.99% |
| credit card | 0.577 | 2.19 | 3,801 | 3.89% | 19.62% |
| debit card | 0.101 | 0.38 | 3,745 | 0.68% | 3.39% |

Average ticket = value in ₹ crore × 10 / volume in millions. Lower UPI P2M
ticket size is consistent with different transaction mixes; it does not show
that a given card transaction switched to UPI.

### Year-on-year pattern counts (39 matched months, Jan 2023–Mar 2026)

| Rail | Absolute contraction | Relative share loss | Co-expansion | Other |
| --- | ---: | ---: | ---: | ---: |
| credit card | 0 | 38 | 1 | 0 |
| debit card | 39 | 0 | 0 | 0 |

## UPI and IMPS: FY2016-17 to FY2025-26

| Rail | FY16-17 volume (bn) | FY25-26 volume (bn) | Volume change | Share change | Pattern |
| --- | ---: | ---: | ---: | ---: | --- |
| IMPS | 0.51 | 4.94 | +875.3% | -94.59 pp | relative share loss |
| UPI total | 0.02 | 241.62 | +1352738.1% | +94.59 pp | co-expansion |

The UPI–IMPS basket is a separate comparison. It should not be added to the
merchant basket because total UPI includes P2M, P2P and other use cases.
UPI's very large percentage growth starts from a near-zero launch base;
the change in transaction counts and basket shares is more informative.

### Most recent full year: FY2024-25 to FY2025-26

| Basket | Rail | Volume change | Share change | Real value change | Pattern |
| --- | --- | ---: | ---: | ---: | --- |
| transfer | IMPS | -12.1% | -0.93 pp | +5.2% | absolute contraction |
| transfer | UPI total | +30.0% | +0.93 pp | +18.2% | co-expansion |
| merchant | UPI P2M | +31.3% | +0.65 pp | +23.9% | co-expansion |
| merchant | credit card | +26.2% | -0.13 pp | +9.8% | relative share loss |
| merchant | debit card | -20.7% | -0.52 pp | -12.0% | absolute contraction |

## Inflation and source checks

MoSPI's linked all-India CPI expresses 2022–26 values and tickets in March
2026 rupees. Nominal shares are unchanged by a common monthly deflator,
although annual real-value shares can differ slightly because monthly rail
composition differs. The panel CSV contains nominal and real measures.

Source coverage: 120 transfer months and 51 merchant months. No missing months were filled.

Maximum absolute source differences (published rounding allowed for
within-source identities):

| Check | Maximum difference |
| --- | ---: |
| CPI linked-index rounding | 2.84217e-14 |
| P2P + P2M value vs ecosystem total (crore) | 0.02 |
| P2P + P2M volume vs ecosystem total (mn) | 0.01 |
| UPI ecosystem vs product value (crore) | 7667.1 |
| UPI ecosystem vs product volume (mn) | 34.88 |
| credit + debit value vs card total (crore) | 1 |
| credit + debit volume vs card total (lakh) | 0.01 |
| credit PoS + Other value (crore) | 1 |
| credit PoS + Other volume (lakh) | 0.01 |
| debit PoS + Other value (crore) | 1 |
| debit PoS + Other volume (lakh) | 0.01 |

NPCI product and ecosystem UPI totals are distinct source tables and
must not be silently substituted for each other. Months with a
difference above 0.11 in either published unit:

| Month | Ecosystem minus product volume (mn) | Ecosystem minus product value (₹ crore) |
| --- | ---: | ---: |
| 2023-04 | +34.88 | -7,667.10 |
| 2023-07 | +0.00 | +108.76 |

## Interpretation limits

These are descriptive associations in national monthly totals. They cannot
identify individual switching, substitution elasticities, merchant acceptance,
or causal network effects. UPI-funded card transactions may overlap the card
series. IMPS and UPI have different use cases; P2M and card totals are not
identical products. RBI own-month release observations are provisional, and the
original RBI workbooks are not yet archived locally. PPI is excluded from the
primary baskets because it mixes uses. The P2P/P2M monthly series was
transcribed from NPCI's official selector table; six original month workbooks
are archived locally. The announced October 2026 merchant
charge is outside this data window.
