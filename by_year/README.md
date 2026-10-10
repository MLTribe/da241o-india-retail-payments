# Calendar-year folders

Each `YYYY/` folder contains the available monthly records for the calendar year. Some series begin later than others; an absent file means that series has not yet been collected for that year, not that the source reported zero. Original fiscal-year workbooks are kept in `../source_archive/npci/` and are split into these folders by the importer.

| Calendar year | Current records |
|---|---|
| 2014–2015 | RBI bank-wise card totals (from Jun 2014) and CPI Combined — long series only |
| 2016–2019 | Total UPI and IMPS (from Apr 2016), RBI bank-wise card totals, CPI Combined |
| 2020 | As 2016–2019, plus P2P/P2M (from Apr) and the PSI prior-year card column (from Jun) |
| 2021 | As 2020, plus RBI cards/PPI and infrastructure from Jun (consistently classified PSI series) |
| 2022 | Total UPI, IMPS, P2P/P2M, RBI cards/PPI, RBI infrastructure, and CPI Combined (12 months each) |
| 2023–2025 | Total UPI, IMPS, P2P/P2M, RBI cards/PPI, RBI infrastructure, and CPI Combined (12 months/year each) |
| 2026 | Total UPI, IMPS, P2P/P2M, RBI cards/PPI, RBI infrastructure, and CPI Combined through March |

P2P/P2M rows are retained as NPCI displayed values in `../source_archive/npci/upi_p2p_p2m_ui_extract_2022-01_2026-03.tsv`; each annual CSV records the source URL and original month label. They were read from the official monthly selector table, not from a complete archive of the one-month XLSX exports.

RBI card and PPI monthly values are in `rbi_psi_card_ppi_own_month.csv` within each year folder. RBI Part III month-end infrastructure counts are in `rbi_psi_infrastructure.csv`, imported by `scripts/import_rbi_psi_pages.py` from each official release page's own-month column. The source URLs and HTML checksums are in `../source_archive/rbi/rbi_psi_infrastructure_own_release_extract_2022-01_2026-03.csv`. Direct official XLSX links are indexed in `../source_archive/rbi/rbi_psi_release_links_2022-01_2026-03.csv`; the original RBI workbooks have not yet been archived. CPI is available in `mospi_cpi_combined_monthly.csv` in each year folder (2014–2026); see the parent README for base-year and linking notes. IIP remains pending and is optional context.

`rbi_psi_infrastructure.csv` holds cards outstanding, PoS terminals, Bharat QR, UPI QR codes and ATMs, all in lakh, for June 2021–March 2026. Cards outstanding count instruments, not unique people or active users. `rbi_psi_other_rails.csv` remains pending; it will hold RTGS, NEFT, AePS fund transfers, NETC and ATM cash withdrawals (volume in lakh, value in ₹ crore) when `scripts/import_rbi_psi_workbooks.py` imports the archived workbooks. The model treats missing series as optional and only adds them to a basket when they cover its whole window.

Files written by `scripts/import_card_history.py` (2014–2021): `rbi_bankwise_card_totals.csv` (all-bank totals of the older-layout RBI bank-wise pages, card counts and payments by debit/credit, values in ₹ crore after unit conversion, `value_unit_published` records the page's own unit), `rbi_psi_card_prior_year_column.csv` (the "same month last year" column of the Jun 2021 – May 2022 PSI pages, used only to check the splice), and the 2014–2021 CPI, Jun–Dec 2021 PSI and Apr 2020 – Dec 2021 P2P/P2M rows. Bank-wise totals are a different classification from PSI and are used only through the spliced long series in `analysis/long_series.py`.
