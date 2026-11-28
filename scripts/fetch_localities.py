"""Fetches the State of Israel's official list of localities (the Central Bureau of Statistics' list on data.gov.il:
symbol, Hebrew name, English name) and writes src/partylinks/localities.json, from which every town takes its
official Hebrew name (partylinks.places). Run when the list changes (a new locality, a renamed one), from the root:

    python scripts/fetch_localities.py
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[1] / "src/partylinks/localities.json"
API = "https://data.gov.il/api/3/action/datastore_search"
RESOURCE = "5c78e9fa-c2e2-4771-93ff-7f400a12f7ba"  # "רשימת ישובים בישראל" (CBS)


def main() -> None:
    result = httpx.get(API, params={"resource_id": RESOURCE, "limit": 5000}, timeout=60).json()["result"]
    rows = [
        {"code": int(r["סמל_ישוב"]), "he": r["שם_ישוב"].strip(), "en": r["שם_ישוב_לועזי"].strip()}
        for r in result["records"]
        if r["שם_ישוב"].strip() and str(r["סמל_ישוב"]).strip().isdigit() and int(r["סמל_ישוב"]) > 0
    ]
    if len(rows) < 1000:
        raise SystemExit(f"only {len(rows)} localities: not written")
    rows.sort(key=lambda r: r["code"])
    payload = {"source": f"data.gov.il resource {RESOURCE} (CBS list of localities)", "localities": rows}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(len(rows), "localities ->", OUT)


if __name__ == "__main__":
    main()
