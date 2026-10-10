#!/usr/bin/env python3
"""Archive NPCI UPI ecosystem-statistics tables (apps, states, member banks) as raw JSON.

NPCI's site rejects scripted HTTP clients, but serves its public statistics API to a
real browser. This script asks headless Google Chrome to open each API URL and saves
the JSON it receives, one file per tab and month, under
source_archive/npci/ecosystem_api/<tab>/<YYYY-MM>.json. Existing files are skipped.

Run from the repository root: python3 scripts/fetch_npci_ecosystem.py [START END [TAB ...]]
Default window: 2022-01 to 2026-03, default tabs TABS. The P2P/P2M tab
(p2p-and-p2m-transactions) is fetched on request; NPCI answers 404 before its first month.
Needs Google Chrome; no third-party package.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "source_archive" / "npci" / "ecosystem_api"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128 Safari/537.36")
API = "https://www.npci.org.in/api/ecosystem-statistics/get-statistics"
TABS = ["upi-apps", "statewise-statistic", "top-50-mem-vol-val"]
MONTH_NAMES = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
# A custom --user-data-dir makes NPCI's bot check hang, and the default headless profile
# cannot be shared between concurrent Chrome processes, so requests run one at a time.
WORKERS = 1
ATTEMPTS = 3
TIMEOUT_S = 60


def months(start: str, end: str) -> list[str]:
    y, m = int(start[:4]), int(start[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= end:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fetch(tab: str, month: str) -> str:
    target = ARCHIVE / tab / f"{month}.json"
    if target.exists() and target.stat().st_size > 0:
        return f"skip {tab} {month}"
    url = (f"{API}?product_name=upi&tab_name={tab}&year={month[:4]}"
           f"&month={MONTH_NAMES[int(month[5:]) - 1]}&page_no=1&size=1000")
    problem = ""
    for _ in range(ATTEMPTS):
        try:
            dom = subprocess.run(
                [CHROME, "--headless=new", "--disable-gpu", "--virtual-time-budget=8000",
                 f"--user-agent={USER_AGENT}", "--dump-dom", url],
                capture_output=True, text=True, timeout=TIMEOUT_S).stdout
        except subprocess.TimeoutExpired:
            problem = "timed out"
            continue
        text = html.unescape(re.sub(r"<[^>]+>", "", dom)).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            problem = f"not JSON ({len(text)} chars)"
            continue
        if payload.get("status") == 404:
            return f"none {tab} {month}: {payload.get('message')}"
        data = payload.get("data") or {}
        rows = data.get("results") or []
        if len(rows) < (data.get("totalCount") or 0):
            problem = f"{len(rows)} of {data.get('totalCount')} rows"
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return f"ok   {tab} {month}: {len(rows)} rows"
    return f"FAIL {tab} {month}: {problem}"


def main() -> None:
    start, end = (sys.argv[1], sys.argv[2]) if len(sys.argv) >= 3 else ("2022-01", "2026-03")
    tabs = sys.argv[3:] or TABS
    jobs = [(tab, m) for tab in tabs for m in months(start, end)]
    with ThreadPoolExecutor(WORKERS) as pool:
        for line in pool.map(lambda job: fetch(*job), jobs):
            print(line, flush=True)


if __name__ == "__main__":
    main()
