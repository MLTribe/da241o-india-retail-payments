# RBI Payment System Indicators source pages

This folder contains the original HTML responses for the 56 monthly RBI Payment System Indicators release pages from January 2022 through August 2026. Filenames identify the observation month and RBI release ID, for example `2022-01_id8.html`.

`../rbi_psi_infrastructure_own_release_extract_2022-01_2026-03.csv` and `../rbi_psi_infrastructure_own_release_extract_2026-04_2026-08.csv` link each observation to its archived page, official URL, and SHA-256 checksum of the HTML bytes. The importer `../../../scripts/import_rbi_psi_pages.py` reads these pages when present and downloads missing pages from RBI. It uses the release month's own column in Part III; Part I card and PPI rows are in the separate card/PPI extracts (April–August 2026 parsed from these pages).

These pages are source evidence. Use the cleaned monthly files in `../../../by_year/YYYY/rbi_psi_infrastructure.csv` for analysis.
