# RBI Payment System Indicators workbooks

Save the official monthly PSI workbooks here from a browser. RBI's download links
sit behind bot protection, so scripted downloads receive a challenge page.

Name each file either with its official XLSX basename (as listed in
`../rbi_psi_release_links_2022-01_2026-03.csv`) or with a leading observation
month, for example `2021-06_PSI.xlsx`. Then run, from the repository root:

```shell
python3 scripts/import_rbi_psi_workbooks.py
```

The importer extracts the own-month column for cards, PPI, RTGS, NEFT, AePS
fund transfers, NETC, ATM cash withdrawals and payment-system infrastructure. It
then writes `by_year/YYYY/rbi_psi_other_rails.csv` and
`by_year/YYYY/rbi_psi_infrastructure.csv`, adds card/PPI months that are not yet
transcribed, cross-checks overlapping months in `../xlsx_crosscheck.csv`, and
records SHA-256 checksums in `source_manifest.csv`. Existing transcribed months
are never overwritten.
