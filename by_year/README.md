# Calendar-year folders

Each `YYYY/` folder contains the available monthly records for the calendar year. Some series begin later than others; an absent file means that series has not yet been collected for that year, not that the source reported zero. Original fiscal-year workbooks are kept in `../source_archive/npci/` and are split into these folders by the importer.

| Calendar year | Current records |
|---|---|
| 2016–2020 | Total UPI and IMPS |
| 2021 | Total UPI and IMPS |
| 2022 | Total UPI, IMPS, P2P/P2M (12 months), RBI cards/PPI (12 months), and CPI Combined (12 months) |
| 2023–2025 | Total UPI, IMPS, P2P/P2M (12 months/year), RBI cards/PPI (12 months/year), and CPI Combined (12 months/year) |
| 2026 | Total UPI, IMPS, P2P/P2M, RBI cards/PPI, and CPI Combined through March |

P2P/P2M rows are retained as NPCI displayed values in `../source_archive/npci/upi_p2p_p2m_ui_extract_2022-01_2026-03.tsv`; each annual CSV records the source URL and original month label. They were read from the official monthly selector table, not from a complete archive of the one-month XLSX exports.

RBI monthly values are in `rbi_psi_card_ppi_own_month.csv` within each year folder. They were transcribed from each release’s own-month table column; direct official XLSX links are indexed in `../source_archive/rbi/rbi_psi_release_links_2022-01_2026-03.csv`. The original RBI workbooks have not yet been archived. CPI is available in `mospi_cpi_combined_monthly.csv` in each year folder (2022–2026); see the parent README for base-year and linking notes. IIP remains pending and is optional context.

`scripts/import_rbi_psi_workbooks.py` writes two more files per year once RBI workbooks are archived. `rbi_psi_other_rails.csv` holds RTGS, NEFT, AePS fund transfers, NETC and ATM cash withdrawals (volume in lakh, value in ₹ crore). `rbi_psi_infrastructure.csv` holds cards outstanding, PoS terminals, Bharat QR, UPI QR codes and ATMs, all in lakh. The model treats both as optional and only adds them to a basket when they cover its whole window.
