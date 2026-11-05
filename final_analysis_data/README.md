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

## Rebuilding

The files are generated, not edited by hand. From the `assignment2` folder:

```shell
python3 analysis/daily_trends.py                 # refreshes the daily data (if a new RBI daily file is added)
python3 analysis/final_analysis.py               # refreshes the monthly panel
python3 scripts/build_final_analysis_data.py     # rewrites this folder
```

The full data set, including series not used here, is in `../by_year/`, and the original downloads are in `../source_archive/`.
