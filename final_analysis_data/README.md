# Final analysis data

Only the data the final analysis uses: the series named in the Assignment 2 data table, monthly, Nov 2017 – Mar 2026 for the main window. Use these files so everyone works from the same numbers.

**Quick start:** open `monthly_panel.csv`. It has one row per month with every series already in million payments, ₹ crore and shares, plus real (inflation-adjusted) values, card stocks, and Diwali and COVID flags. `data_dictionary.csv` explains each column. The raw files in `npci/`, `rbi/` and `mospi/` are only needed if you want to rebuild a series yourself.

## Folders

| File | Contents | Period | Units |
| --- | --- | --- | --- |
| `monthly_panel.csv` | Analysis-ready panel built from the files below | Nov 2017 – Mar 2026 | see `data_dictionary.csv` |
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
- **Daily-based totals** are for checks only. They agree with NPCI's monthly figures to within 1%, and they extend UPI and IMPS to Sep 2026.
- **Shares** use these groups: UPI + IMPS (`_ui_`), P2M + debit + credit (`_pc_`), UPI total + debit + credit (`_uc_`), and debit + credit (`_cc_`).
- Each raw file keeps a source column (page URL or archived file) so any value can be traced back.

## Rebuilding

The files are generated, not edited by hand. From the `assignment2` folder:

```shell
python3 analysis/final_analysis.py               # refreshes the panel
python3 scripts/build_final_analysis_data.py     # rewrites this folder
```

The full data set, including series not used here, is in `../by_year/`, and the original downloads are in `../source_archive/`.
