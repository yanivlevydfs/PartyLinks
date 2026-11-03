"""Box offices inside a multi-store source: each Smarticket or PRES tenant (a city's culture hall, a
theatre, a museum), each municipal / community-centre site, is its own store to the people buying
there, though one source reads them all.

The stores page lists sources; this adds a row per configured host: its name (from config), its site,
and how many upcoming events it sells. Events are told apart by the host of their URL, so no column or
table is needed.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import BaseModel
from sqlalchemy import select

from partylinks.db import EventRow, upcoming_filter
from partylinks.scrapers.eventpro import EventProSettings
from partylinks.scrapers.matnasnet import MatnasnetSettings
from partylinks.scrapers.munievents import MuniEventsSettings
from partylinks.scrapers.onecity import OneCitySettings
from partylinks.scrapers.pres import PresSettings
from partylinks.scrapers.smarticket import SmarticketSettings, tenant_host

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from partylinks.scrapers.base import SourceSettings


class BoxOfficeOut(BaseModel):
    source: str  # the source that reads it ("smarticket")
    name: str  # as configured: "היכל התרבות כרמיאל"
    city: str | None
    url: str  # its own site, https://<tenant host>
    events: int  # upcoming events it sells (each event group once)


def _hosts(settings: SourceSettings) -> list[tuple[str, str, str | None]]:
    """(host, name, city) of every store a multi-store source reads; [] for a one-site source."""
    if isinstance(settings, SmarticketSettings):
        return [
            (h, t.venue_name or h, t.city)
            for t in settings.tenants
            if (h := tenant_host(t.host, settings.tenant_domain))
        ]
    if isinstance(settings, PresSettings):
        return [(h, x.name or x.venue or h, x.city) for x in settings.hosts if (h := urlsplit(x.url).hostname)]
    if isinstance(settings, MuniEventsSettings):
        return [
            (h, x.organizer or f"עיריית {x.city}", x.city) for x in settings.sites if (h := urlsplit(x.url).hostname)
        ]
    if isinstance(settings, MatnasnetSettings):
        return [(h, f'מתנ"ס {x.city}', x.city) for x in settings.sites if (h := urlsplit(x.url).hostname)]
    if isinstance(settings, EventProSettings):
        return [(h, x.name, x.city) for x in settings.sites if (h := urlsplit(x.url).hostname)]
    if isinstance(settings, OneCitySettings):
        return [(h, f"עיריית {x.city}", x.city) for x in settings.sites if (h := urlsplit(x.url).hostname)]
    return []


def box_offices(session: Session, sources: dict[str, SourceSettings]) -> list[BoxOfficeOut]:
    """Every configured store of every enabled multi-store source, most upcoming events first."""
    offices: dict[str, BoxOfficeOut] = {}
    for source, settings in sources.items():
        if not settings.enabled or not (hosts := _hosts(settings)):
            continue
        rows = session.execute(
            select(EventRow.group_id, EventRow.url).where(EventRow.source == source, upcoming_filter()).distinct()
        ).all()
        counts = Counter(host for _, host in {(group, urlsplit(url).hostname) for group, url in rows})
        for host, name, city in hosts:
            offices[host] = BoxOfficeOut(
                source=source, name=name, city=city, url=f"https://{host}", events=counts.get(host, 0)
            )
    return sorted(offices.values(), key=lambda o: (-o.events, o.name))
