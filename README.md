# Assignment 2 source data

This folder is the home for DA241o Assignment 2 inputs. The existing `data/` files at the parent level belong to the earlier smartphone project and are not part of the UPI assignment.

## Organization

- `by_year/YYYY/` contains cleaned monthly extracts assigned by **observation calendar year**. Use these files for analysis.
- `source_archive/npci/`, `source_archive/rbi/`, and `source_archive/mospi/` contain official downloads or source extracts. Fiscal-year files remain here in their original form even when their monthly observations are split across calendar-year folders. The UPI P2P/P2M raw TSV is an NPCI table transcription, not an original workbook; its format is documented in the manifest.
- `source_manifest.csv` records source coverage, download status, retrieval date, filename, and SHA-256 for every collected original.

## Required source series and intended coverage

| Series | Planned coverage | Current status |
|---|---|---|
| NPCI total UPI | April 2016–March 2026, for long-run context and UPI–IMPS comparison | Complete fiscal-year originals FY2016–17 to FY2025–26 collected; 120 monthly rows split across 2016–2026 |
| NPCI IMPS | April 2016–March 2026 | Complete; FY2016–17 to FY2025–26 originals collected; 120 monthly rows split across 2016–2026 |
| NPCI UPI P2P/P2M | January 2022–March 2026 common window | Complete; 51 monthly observations captured from NPCI's month/year-selected table, retained raw in `source_archive/npci/upi_p2p_p2m_ui_extract_2022-01_2026-03.tsv` and split into annual CSVs |
| RBI domestic cards and PPI components | January 2022–March 2026 | 51 own-month release-page observations transcribed and split by calendar year. Direct official workbook links are indexed, but original RBI XLSX files are not archived yet. |
| MoSPI CPI Combined | January 2022–March 2026 | Complete: 51 monthly values, with original index base, official Combined link factor and source release URLs retained in annual and archive CSVs. MoSPI source PDFs are referenced but not downloaded. |
| MoSPI IIP general index | At least January 2022–March 2026 for activity context | Optional context; not collected for the core comparison |

The sources do not all start in the same month. Do not fill gaps by interpolation or mix rails into a denominator where coverage differs. The CPI extract records native 2012-base indices through Dec 2025 and native 2024-base indices for Jan–Mar 2026; a separate linked 2024-base column applies MoSPI’s official 0.5267 Combined general-index linking factor to the 2012-base observations. Dec 2025 is marked provisional in the old-base table. CPI values were transcribed from official MoSPI release PDFs; the PDF binaries are not archived. The final source freeze and common windows will be logged before analysis.

## Importing annual NPCI workbooks

After adding original NPCI product-statistics workbooks to `source_archive/npci/`, run:

```shell
python3 scripts/import_assignment2_npci_workbooks.py
```

The importer splits monthly rows into `by_year/YYYY/`, preserves units, retains source filenames and URLs, sorts months chronologically, and writes checksums to the source manifest.

## RBI extraction notes

`source_archive/rbi/rbi_psi_card_ppi_monthly_own_release_extract_2022-01_2026-03.csv` contains 51 monthly rows transcribed from the current-month column of each RBI Payment System Indicators release page. It covers total cards, credit/debit cards and their PoS/Other splits, plus PPI, wallets, and PPI cards with PoS/Other splits. `rbi_psi_release_links_2022-01_2026-03.csv` maps every observation month to the official release page and linked XLSX. The original RBI workbook files have not yet been downloaded into the archive. Values use the published units: volume in lakh and value in ₹ crore; ₹ crore components may differ from parent totals by ₹1 due to rounding.
