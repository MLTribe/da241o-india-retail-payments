#!/usr/bin/env python3
"""Archive RBI "Bank-wise ATM/POS/Card Statistics" monthly HTML pages.

Each page is www.rbi.org.in/Scripts/ATMView.aspx?atmid=<n>; ids run roughly one per month but
not strictly in order (atmid 40 = June 2014, 131 = January 2022, 181 = March 2026), so importers
read the month from the page heading. Pages are saved
unchanged as source_archive/rbi/bankwise_html/atmid_<n>.html; existing files are skipped.
RBI's XLSX links are bot-blocked, but the HTML pages are served to plain clients.

Run from the repository root: python3 scripts/fetch_rbi_bankwise_html.py [FIRST LAST]
Default ids: 40 to 181. Standard library only; requests are sequential and paced.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "source_archive" / "rbi" / "bankwise_html"
URL = "https://www.rbi.org.in/Scripts/ATMView.aspx?atmid={}"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128 Safari/537.36")


def fetch(atmid: int) -> str:
    target = ARCHIVE / f"atmid_{atmid}.html"
    if target.exists() and target.stat().st_size > 20_000:
        return f"skip {atmid}"
    for attempt in range(3):
        try:
            with urlopen(Request(URL.format(atmid), headers={"User-Agent": USER_AGENT}), timeout=60) as r:
                raw = r.read()
            if len(raw) > 20_000:
                target.write_bytes(raw)
                return f"ok   {atmid}: {len(raw)} bytes"
        except OSError as exc:
            problem = str(exc)
        time.sleep(2 ** attempt)
    return f"FAIL {atmid}: {problem if 'problem' in locals() else 'short response'}"


def main() -> None:
    first, last = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) == 3 else (40, 181)
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    for atmid in range(first, last + 1):
        print(fetch(atmid), flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
