"""Box offices inside a ticketing platform: each Smarticket tenant (a city's culture hall, a council, a
theatre) is its own store to the people buying there, though one source reads them all.

The stores page lists sources; this adds a row per tenant: its name (the tenant's ``venue_name`` in
config), its site, and how many upcoming events it sells. Events are told apart by the host of their
URL (``<tenant>.smarticket.co.il/event/<id>``), so no column or table is needed.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import BaseModel
from sqlalchemy import select

from partylinks.db import EventRow, upcoming_filter
from partylinks.scrapers.smarticket import SmarticketSettings, tenant_host

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from partylinks.scrapers.base import SourceSettings

SOURCE = "smarticket"


class BoxOfficeOut(BaseModel):
    source: str  # the source that reads it ("smarticket")
    name: str  # as configured: "היכל התרבות כרמיאל"
    city: str | None
    url: str  # its own site, https://<tenant host>
    events: int  # upcoming events it sells (each event group once)


def box_offices(session: Session, sources: dict[str, SourceSettings]) -> list[BoxOfficeOut]:
    """Every configured tenant of an enabled Smarticket source, most upcoming events first."""
    settings = sources.get(SOURCE)
    if not isinstance(settings, SmarticketSettings) or not settings.enabled:
        return []
    rows = session.execute(
        select(EventRow.group_id, EventRow.url).where(EventRow.source == SOURCE, upcoming_filter()).distinct()
    ).all()
    counts = Counter(host for _, host in {(group, urlsplit(url).hostname) for group, url in rows})
    offices = {
        host: BoxOfficeOut(
            source=SOURCE, name=t.venue_name or host, city=t.city, url=f"https://{host}", events=counts.get(host, 0)
        )
        for t in settings.tenants
        if (host := tenant_host(t.host, settings.tenant_domain))
    }
    return sorted(offices.values(), key=lambda o: (-o.events, o.name))
