"""Our own count of ticket-link clicks (GET /go/...): how many, per day, per seller, per spot on
the page and per event. Server-side only, so no analytics cookies and no consent needed.

Sellers see the same traffic in their analytics through the UTM tags (partylinks.utm).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from partylinks.db import ClickRow, EventRow, utcnow
from partylinks.utm import PLACEMENTS


def record(session: Session, row: EventRow, placement: str | None) -> None:
    session.add(
        ClickRow(
            event_id=row.id,
            source=row.source,
            title=row.title,
            city=row.city,
            placement=placement if placement in PLACEMENTS else None,
        )
    )


def stats(session: Session, timezone: str, days: int = 30, top: int = 50) -> dict[str, Any]:
    """Click counts over the last ``days`` days, days in the site's time zone (oldest first)."""
    # ponytail: counts in Python over the window's rows; move to SQL GROUP BY once that's millions.
    zone = ZoneInfo(timezone)
    since = utcnow() - timedelta(days=days)
    rows = session.execute(
        select(ClickRow.at, ClickRow.event_id, ClickRow.title, ClickRow.source, ClickRow.city, ClickRow.placement)
        .where(ClickRow.at >= since)
    ).all()  # fmt: skip
    today = datetime.now(zone).date()
    per_day = Counter(r.at.astimezone(zone).date().isoformat() for r in rows)
    events: Counter[str] = Counter(r.event_id for r in rows)
    titles = {r.event_id: (r.title, r.source) for r in rows}
    return {
        "days": days,
        "total": len(rows),
        "today": per_day[today.isoformat()],
        "per_day": [
            {"date": d, "clicks": per_day[d]}
            for d in ((today - timedelta(days=n)).isoformat() for n in reversed(range(days)))
        ],
        "per_source": dict(Counter(r.source for r in rows).most_common()),
        "per_placement": dict(Counter(r.placement or "unknown" for r in rows).most_common()),
        "per_city": dict(Counter(r.city or "unknown" for r in rows).most_common(20)),
        "top_events": [
            {"event_id": e, "title": titles[e][0], "source": titles[e][1], "clicks": n}
            for e, n in events.most_common(top)
        ],
    }
