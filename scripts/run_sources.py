"""Start sources on the live server now, through the admin API.

    railway run --service web -- python scripts/run_sources.py              # every enabled source
    railway run --service web -- python scripts/run_sources.py shotgun vibez
    railway run --service web -- python scripts/run_sources.py --runs       # latest runs
    railway run --service web -- python scripts/run_sources.py --runs vibez # one source's runs, with errors
    railway run --service web -- python scripts/run_sources.py --report     # per source, last 24 h (--report 72)

``railway run`` hands this script the service's variables, so the admin token
(PARTYLINKS_ADMIN__TOKEN) is used without being printed or stored anywhere.
The URL is the service's public domain (RAILWAY_PUBLIC_DOMAIN), or PARTYLINKS_URL.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta


def call(method: str, path: str, body: dict | None = None) -> object:
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    base = os.environ.get("PARTYLINKS_URL") or (f"https://{domain}" if domain else "http://localhost:8000")
    token = os.environ.get("PARTYLINKS_ADMIN__TOKEN", "")
    request = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def report(hours: int) -> None:
    """Per source over the last ``hours``: runs by status, events found by the last good run,
    time taken, and the latest error. Problems first."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    runs = [r for r in call("GET", "/runs?limit=500") if datetime.fromisoformat(r["started_at"]) >= since]  # type: ignore[union-attr]
    by_source: dict[str, list[dict]] = defaultdict(list)
    for run in runs:  # newest first
        by_source[run["source"]].append(run)
    totals = Counter(r["status"] for r in runs)
    print(f"last {hours} h: {len(runs)} runs, {len(by_source)} sources | " + ", ".join(f"{n} {s}" for s, n in totals.most_common()))
    rows = []
    for source, items in by_source.items():
        count = Counter(r["status"] for r in items)
        good = next((r for r in items if r["status"] in ("success", "partial")), None)
        done = [r for r in items if r.get("finished_at")]
        minutes = sum(
            (datetime.fromisoformat(r["finished_at"]) - datetime.fromisoformat(r["started_at"])).total_seconds() for r in done
        ) / 60 / max(len(done), 1)
        error = next((r["error"] for r in items if r.get("error")), "") or ""
        bad = count["failed"] + count["blocked"]
        rows.append((bad == 0, -bad, source, count, good["events_found"] if good else 0, minutes, error))
    print(f"{'source':<18} {'ok':>3} {'part':>4} {'fail':>4} {'block':>5} {'found':>6} {'min':>5}  last error")
    for _, _, source, count, found, minutes, error in sorted(rows):
        line = error.splitlines()[0][:90] if error else ""
        print(f"{source:<18} {count['success']:>3} {count['partial']:>4} {count['failed']:>4} {count['blocked']:>5} {found:>6} {minutes:>5.1f}  {line}")


def main(args: list[str]) -> None:
    if args[:1] == ["--report"]:
        report(int(args[1]) if len(args) > 1 else 24)
        return
    if args[:1] == ["--runs"]:
        query = f"/runs?limit=10&source={args[1]}" if len(args) > 1 else "/runs?limit=20"
        for run in call("GET", query):  # type: ignore[union-attr]
            error = f"  {run['error'][:200]}" if run.get("error") else ""
            print(f"{run['started_at'][:16]}  {run['source']:<18} {run['status']:<11} found {run['events_found']}{error}")
        return
    print(json.dumps(call("POST", "/admin/api/scan", {"sources": args} if args else {}), indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
