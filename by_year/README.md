# Calendar-year folders

Each `YYYY/` folder contains the available monthly records for the calendar year. Some series begin later than others; an absent file means that series has not yet been collected for that year, not that the source reported zero. Original fiscal-year workbooks are kept in `../source_archive/npci/` and are split into these folders by the importer.

| Calendar year | Current records |
|---|---|
| 2016–2020 | Total UPI and IMPS |
| 2021 | Total UPI and IMPS |
| 2022 | Total UPI, IMPS, P2P/P2M, RBI cards/PPI, RBI infrastructure, and CPI Combined (12 months each) |
| 2023–2025 | Total UPI, IMPS, P2P/P2M, RBI cards/PPI, RBI infrastructure, and CPI Combined (12 months/year each) |
| 2026 | Total UPI, IMPS, P2P/P2M, RBI cards/PPI, RBI infrastructure, and CPI Combined through March |

P2P/P2M rows are retained as NPCI displayed values in `../source_archive/npci/upi_p2p_p2m_ui_extract_2022-01_2026-03.tsv`; each annual CSV records the source URL and original month label. They were read from the official monthly selector table, not from a complete archive of the one-month XLSX exports.

RBI card and PPI monthly values are in `rbi_psi_card_ppi_own_month.csv` within each year folder. RBI Part III month-end infrastructure counts are in `rbi_psi_infrastructure.csv`, imported by `scripts/import_rbi_psi_pages.py` from each official release page's own-month column. The source URLs and HTML checksums are in `../source_archive/rbi/rbi_psi_infrastructure_own_release_extract_2022-01_2026-03.csv`. Direct official XLSX links are indexed in `../source_archive/rbi/rbi_psi_release_links_2022-01_2026-03.csv`; the original RBI workbooks have not yet been archived. CPI is available in `mospi_cpi_combined_monthly.csv` in each year folder (2022–2026); see the parent README for base-year and linking notes. IIP remains pending and is optional context.

`rbi_psi_infrastructure.csv` holds cards outstanding, PoS terminals, Bharat QR, UPI QR codes and ATMs, all in lakh, for January 2022–March 2026. Cards outstanding count instruments, not unique people or active users. June–December 2021 infrastructure observations and `rbi_psi_other_rails.csv` remain pending; the latter will hold RTGS, NEFT, AePS fund transfers, NETC and ATM cash withdrawals (volume in lakh, value in ₹ crore) when `scripts/import_rbi_psi_workbooks.py` imports the archived workbooks. The model treats missing series as optional and only adds them to a basket when they cover its whole window.
