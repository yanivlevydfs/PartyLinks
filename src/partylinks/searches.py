"""Our own count of searches on the site (GET /events?q=...&sponsored=true, first page: the results grid): how many, per day, what
people look for and what finds nothing. No IP, cookie or user agent: the words, the result count, when.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from itertools import islice, takewhile
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from partylinks.db import Database, SearchRow, utcnow

log = logging.getLogger(__name__)

# The search box sends after 250 ms of quiet, so "טכנ" then "טכנו" is one person typing, not two searches.
TYPING_GAP = timedelta(seconds=30)


def normalize(q: str) -> str:
    return " ".join(q.split()).casefold()[:100]


def record(db: Database, q: str, results: int) -> None:
    """After the response (a background task): a search never waits on the write lock."""
    try:
        with db.session() as session:
            session.add(SearchRow(q=normalize(q), results=results))
    except Exception as exc:  # a lost search is not worth a failed request
        log.warning("search not recorded: %s", exc)


def typed(rows: Sequence[Any]) -> list[Any]:
    """Drops the partial words of someone still typing: a search that a longer one starting with it
    follows within TYPING_GAP. ``rows`` oldest first."""
    # ponytail: two visitors typing the same prefix within 30 s merge into one; fine at today's traffic.
    kept = []
    for i, r in enumerate(rows):
        later = takewhile(lambda n: n.at - r.at <= TYPING_GAP, islice(rows, i + 1, None))  # only the next 30 s
        if not any(n.q != r.q and n.q.startswith(r.q) for n in later):
            kept.append(r)
    return kept


def stats(session: Session, timezone: str, days: int = 30, top: int = 50) -> dict[str, Any]:
    """Search counts over the last ``days`` days, days in the site's time zone (oldest first)."""
    # ponytail: counts in Python over the window's rows, like clicks; SQL GROUP BY once that's millions.
    zone = ZoneInfo(timezone)
    since = utcnow() - timedelta(days=days)
    rows = typed(session.execute(select(SearchRow.at, SearchRow.q, SearchRow.results).where(SearchRow.at >= since).order_by(SearchRow.at)).all())
    today = datetime.now(zone).date()
    per_day = Counter(r.at.astimezone(zone).date().isoformat() for r in rows)
    terms = Counter(r.q for r in rows)
    found = {r.q: r.results for r in rows}  # the latest count of each
    misses = Counter(r.q for r in rows if not r.results)
    return {
        "days": days,
        "total": len(rows),
        "today": per_day[today.isoformat()],
        "distinct": len(terms),
        "no_results": sum(misses.values()),
        "per_day": [
            {"date": d, "searches": per_day[d]}
            for d in ((today - timedelta(days=n)).isoformat() for n in reversed(range(days)))
        ],
        "top": [{"q": q, "searches": n, "results": found[q]} for q, n in terms.most_common(top)],
        "top_misses": [{"q": q, "searches": n} for q, n in misses.most_common(top)],
        "recent": [{"at": r.at, "q": r.q, "results": r.results} for r in reversed(rows[-top:])],
    }


if __name__ == "__main__":
    from types import SimpleNamespace as R

    t = utcnow()
    rows = [R(at=t, q="טכ"), R(at=t + timedelta(seconds=1), q="טכנו"), R(at=t + timedelta(minutes=5), q="טכ")]
    assert [r.q for r in typed(rows)] == ["טכנו", "טכ"], typed(rows)
    assert normalize("  Techno   TLV ") == "techno tlv"
    print("ok")
