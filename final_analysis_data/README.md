# Final analysis data

Only the data the final analysis uses: the series named in the Assignment 2 data table, monthly, Nov 2017 – Mar 2026 for the main window. Use these files so everyone works from the same numbers.

**Quick start:** open `monthly_panel.csv`. It has one row per month with every series already in million payments, ₹ crore and shares, plus real (inflation-adjusted) values, card stocks, and Diwali and COVID flags. `data_dictionary.csv` explains each column. For day-level work (weekdays, festivals, short-run timing) use `daily_panel.csv`. The raw files in `npci/`, `rbi/` and `mospi/` are only needed if you want to rebuild a series yourself.

## Folders

| File | Contents | Period | Units |
| --- | --- | --- | --- |
| `monthly_panel.csv` | Analysis-ready panel built from the files below | Nov 2017 – Mar 2026 | see `data_dictionary.csv` |
| `daily_panel.csv` | One row per day from RBI's daily data: UPI, IMPS, credit and debit card volume, value and average payment; UPI/IMPS and debit/credit shares; weekday, festival day and month (with names) and COVID flags | 1 Nov 2020 – 13 Oct 2026 | million; ₹ crore; ₹ |
| `festivals.csv` | Dates of the major festivals behind the daily festival flags: Holi, Eid al-Fitr, Eid al-Adha (Bakrid), Janmashtami, Dussehra, Diwali, Guru Nanak Jayanti, Christmas | 2020 – 2026 | |
| `npci/upi.csv` | UPI total: number and value of payments | Apr 2016 – Mar 2026 | million; ₹ crore |
| `npci/imps.csv` | IMPS: number and value of payments | Apr 2016 – Mar 2026 | million; ₹ crore |
| `npci/upi_p2p_p2m.csv` | UPI person-to-person (P2P) and person-to-merchant (P2M) | Apr 2020 – Mar 2026 | million; ₹ crore |
| `rbi/cards_psi.csv` | Credit and debit card payments, total, in-store (`pos`) and online (`other`), RBI Payment System Indicators | Jun 2021 – Mar 2026 | lakh; ₹ crore |
| `rbi/cards_bankwise_2014_2022.csv` | Card payments, cards issued and PoS terminals, all-bank totals of RBI's older bank-wise pages | Jun 2014 – Feb 2022 | units; ₹ crore |
| `rbi/card_acceptance.csv` | Cards outstanding, PoS terminals, Bharat QR and UPI QR codes | Jun 2021 – Mar 2026 | lakh |
| `rbi/daily_based_upi_imps.csv` | UPI and IMPS monthly totals summed from RBI's daily data (robustness only) | Nov 2020 – Sep 2026 | million; ₹ crore |
| `mospi/cpi.csv` | CPI Combined, all-India, linked to base 2024 = 100 | Jan 2014 – Mar 2026 | index |
| `events.csv` | Dated breaks, policy changes and COVID waves, with how each is handled | | |
| `manifest.csv` | Every file with its period, row count and SHA-256 checksum | | |

## Things to know before using the numbers

- **Units differ by source.** 1 lakh = 0.1 million; 1 crore = 10 million. NPCI volumes are already in million; RBI PSI volumes are in lakh; the older bank-wise file has raw counts.
- **Card series before Jun 2021.** RBI changed how it classifies card payments in Jun 2021. In `monthly_panel.csv` the older bank-wise totals are scaled to the new series using the months both cover (Jun 2021 – Feb 2022), which is why `cards_bankwise_2014_2022.csv` runs to Feb 2022. Don't join the raw files end to end.
- **Real values** are at March 2026 prices, deflated by `mospi/cpi.csv`.
- **P2M starts in Apr 2020**, so anything using merchant payments has 72 months, not 101.
- **Daily panel cards are card-network payments only.** RBI's daily file counts card payments processed by card networks, so it shows fewer card payments than the monthly figures (in Mar 2026 about 56% fewer credit-card and 9% fewer debit-card payments). Use the daily card columns for timing (weekdays, festivals, short-run changes), and `monthly_panel.csv` for card levels. The daily panel has no real values because CPI is monthly and ends in Mar 2026. Its UPI and IMPS days add up to the monthly totals in `rbi/daily_based_upi_imps.csv`.
- **Festival flags (daily panel).** `festival_day` and `festival_name` mark the festival itself; `festival_month` is 1 when any listed festival falls in that month, and `festival_month_names` lists them. Dates follow the Government of India central holiday list (as encoded in the `holidays` Python package, India calendar). Eid dates depend on the moon and can differ by a day by region. National days (Republic Day, Independence Day, Gandhi Jayanti) are not counted as festivals. `monthly_panel.csv` keeps its `diwali` flag, because the final analysis regressions use a Diwali dummy.
- **Daily-based totals** are for checks only. They agree with NPCI's monthly figures to within 1%, and they extend UPI and IMPS to Sep 2026.
- **Shares** use these groups: UPI + IMPS (`_ui_`), P2M + debit + credit (`_pc_`), UPI total + debit + credit (`_uc_`), and debit + credit (`_cc_`).
- Each raw file keeps a source column (page URL or archived file) so any value can be traced back.

## monthly_panel.csv: column reference

Every column, in file order. Formulas use these terms:

- **Real value** = nominal value × CPI(Mar 2026) ÷ CPI(month), with `cpi_combined_index_linked_2024` from `mospi/cpi.csv`.
- **Real ticket** = real value × 10 ÷ volume (₹ crore per million payments × 10 = ₹ per payment).
- **Card splice.** From Jun 2021 card payments are the PSI totals (`*_total_volume_lakh` ÷ 10, `*_total_value_crore`; total = in-store + online). Before Jun 2021 they are the bank-wise totals (`*_payments_n` ÷ 1e6, `*_payments_v_crore`) times k, the geometric mean of PSI ÷ bank-wise over Jun 2021 – Feb 2022, worked out separately for each series (k = 1.0035 debit volume, 1.0094 debit value, 1.0000 credit volume and value).
- **Share** = own volume ÷ sum of the group's volumes × 100, only for months where every group member has data. Shares use volumes, never values.

P2M columns and the `_pc_` shares are blank before Apr 2020 (Nov 2017 – Mar 2020, 29 months). No other column has blanks.

| Column | What it is | Unit | Source | Derivation |
| --- | --- | --- | --- | --- |
| `month` | Calendar month | YYYY-MM | built | Nov 2017 – Mar 2026 |
| `upi_volume_mn` | UPI payments, total | million | NPCI, `npci/upi.csv` | `volume_mn` as published |
| `upi_value_crore` | UPI value, nominal | ₹ crore | NPCI, `npci/upi.csv` | `value_crore` as published |
| `upi_real_value_crore` | UPI value at Mar 2026 prices | ₹ crore | NPCI + MoSPI, `npci/upi.csv`, `mospi/cpi.csv` | real value of `upi_value_crore` |
| `upi_real_ticket_rs` | UPI average payment at Mar 2026 prices | ₹ | built | `upi_real_value_crore` × 10 ÷ `upi_volume_mn` |
| `imps_volume_mn` | IMPS payments | million | NPCI, `npci/imps.csv` | `volume_mn` as published |
| `imps_value_crore` | IMPS value, nominal | ₹ crore | NPCI, `npci/imps.csv` | `value_crore` as published |
| `imps_real_value_crore` | IMPS value at Mar 2026 prices | ₹ crore | NPCI + MoSPI, `npci/imps.csv`, `mospi/cpi.csv` | real value of `imps_value_crore` |
| `imps_real_ticket_rs` | IMPS average payment at Mar 2026 prices | ₹ | built | `imps_real_value_crore` × 10 ÷ `imps_volume_mn` |
| `p2m_volume_mn` | UPI person-to-merchant payments | million | NPCI, `npci/upi_p2p_p2m.csv` | `p2m_volume_mn` as published; from Apr 2020 |
| `p2m_value_crore` | UPI person-to-merchant value, nominal | ₹ crore | NPCI, `npci/upi_p2p_p2m.csv` | `p2m_value_crore` as published; from Apr 2020 |
| `p2m_real_value_crore` | P2M value at Mar 2026 prices | ₹ crore | NPCI + MoSPI, `npci/upi_p2p_p2m.csv`, `mospi/cpi.csv` | real value of `p2m_value_crore` |
| `p2m_real_ticket_rs` | P2M average payment at Mar 2026 prices | ₹ | built | `p2m_real_value_crore` × 10 ÷ `p2m_volume_mn` |
| `debit_volume_mn` | Debit-card payments, in-store + online | million | RBI, `rbi/cards_psi.csv` (from Jun 2021), `rbi/cards_bankwise_2014_2022.csv` (before) | card splice: `debit_total_volume_lakh` ÷ 10; before Jun 2021 `debit_payments_n` ÷ 1e6 × 1.0035 |
| `debit_value_crore` | Debit-card payment value, nominal | ₹ crore | RBI, same two files | card splice: `debit_total_value_crore`; before Jun 2021 `debit_payments_v_crore` × 1.0094 |
| `debit_real_value_crore` | Debit-card value at Mar 2026 prices | ₹ crore | built + MoSPI, `mospi/cpi.csv` | real value of `debit_value_crore` |
| `debit_real_ticket_rs` | Debit-card average payment at Mar 2026 prices | ₹ | built | `debit_real_value_crore` × 10 ÷ `debit_volume_mn` |
| `credit_volume_mn` | Credit-card payments, in-store + online | million | RBI, `rbi/cards_psi.csv` (from Jun 2021), `rbi/cards_bankwise_2014_2022.csv` (before) | card splice: `credit_total_volume_lakh` ÷ 10; before Jun 2021 `credit_payments_n` ÷ 1e6 × k (k ≈ 1) |
| `credit_value_crore` | Credit-card payment value, nominal | ₹ crore | RBI, same two files | card splice: `credit_total_value_crore`; before Jun 2021 `credit_payments_v_crore` × k (k ≈ 1) |
| `credit_real_value_crore` | Credit-card value at Mar 2026 prices | ₹ crore | built + MoSPI, `mospi/cpi.csv` | real value of `credit_value_crore` |
| `credit_real_ticket_rs` | Credit-card average payment at Mar 2026 prices | ₹ | built | `credit_real_value_crore` × 10 ÷ `credit_volume_mn` |
| `share_upi_ui_pct` | UPI share of UPI + IMPS | % | built | upi ÷ (upi + imps) × 100 |
| `share_imps_ui_pct` | IMPS share of UPI + IMPS | % | built | imps ÷ (upi + imps) × 100 |
| `share_p2m_pc_pct` | P2M share of P2M + debit + credit | % | built | p2m ÷ (p2m + debit + credit) × 100; from Apr 2020 |
| `share_debit_pc_pct` | Debit share of P2M + debit + credit | % | built | debit ÷ (p2m + debit + credit) × 100; from Apr 2020 |
| `share_credit_pc_pct` | Credit share of P2M + debit + credit | % | built | credit ÷ (p2m + debit + credit) × 100; from Apr 2020 |
| `share_upi_uc_pct` | UPI total share of UPI + debit + credit | % | built | upi ÷ (upi + debit + credit) × 100 |
| `share_debit_uc_pct` | Debit share of UPI + debit + credit | % | built | debit ÷ (upi + debit + credit) × 100 |
| `share_credit_uc_pct` | Credit share of UPI + debit + credit | % | built | credit ÷ (upi + debit + credit) × 100 |
| `share_debit_cc_pct` | Debit share of card payments | % | built | debit ÷ (debit + credit) × 100 |
| `share_credit_cc_pct` | Credit share of card payments | % | built | credit ÷ (debit + credit) × 100 |
| `credit_cards_lakh` | Credit cards outstanding | lakh | RBI, `rbi/card_acceptance.csv` (from Jun 2021), `rbi/cards_bankwise_2014_2022.csv` (before) | `credit_cards_outstanding`; before Jun 2021 `credit_cards` ÷ 1e5. Not scaled |
| `debit_cards_lakh` | Debit cards outstanding | lakh | RBI, same two files | `debit_cards_outstanding`; before Jun 2021 `debit_cards` ÷ 1e5. Not scaled |
| `pos_terminals_lakh` | PoS terminals (card machines) | lakh | RBI, same two files | `pos_terminals` (PSI); before Jun 2021 bank-wise `pos_terminals` ÷ 1e5. Not scaled |
| `debit_atm_withdrawals_mn` | Debit-card ATM cash withdrawals (cash context) | million | RBI bank-wise card statistics; not in this folder (see derivation) | to Feb 2022: all-bank `debit_atm_n` ÷ 1e6 from `../by_year/*/rbi_bankwise_card_totals.csv`; from Mar 2022: sum over banks of `debit_atm_cash_n` ÷ 1e6 from `../by_year/*/rbi_bankwise_cards.csv`. Not scaled |
| `diwali` | Diwali (Lakshmi Puja) month | 0/1 | built | 1 in 2018-11, 2019-10, 2020-11, 2021-11, 2022-10, 2023-11, 2024-10, 2025-10 |
| `covid` | COVID wave month | 0/1 | built | 1 in Mar – Jun 2020 and Apr – Jun 2021 |

## Rebuilding

The files are generated, not edited by hand. From the `assignment2` folder:

```shell
python3 analysis/daily_trends.py                 # refreshes the daily data (if a new RBI daily file is added)
python3 analysis/final_analysis.py               # refreshes the monthly panel
python3 scripts/build_final_analysis_data.py     # rewrites this folder
```

The full data set, including series not used here, is in `../by_year/`, and the original downloads are in `../source_archive/`.
