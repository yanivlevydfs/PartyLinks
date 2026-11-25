"""Box offices inside a multi-store source: each Smarticket or PRES tenant (a city's culture hall, a
theatre, a museum), each municipal / community-centre site, is its own store to the people buying
there, though one source reads them all.

The stores page lists sources; this adds a row per configured host: its name (from config), its site,
and how many upcoming events it sells. Events are told apart by the host of their URL, so no column or
table is needed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from pydantic import BaseModel
from sqlalchemy import select

from partylinks.db import EventRow, upcoming_filter
from partylinks.scrapers.eventpro import EventProSettings
from partylinks.scrapers.grayclub import GrayclubSettings
from partylinks.scrapers.matnasnet import MatnasnetSettings
from partylinks.scrapers.munievents import MuniEventsSettings
from partylinks.scrapers.onecity import OneCitySettings
from partylinks.scrapers.pres import PresSettings
from partylinks.scrapers.smarticket import SmarticketSettings, tenant_host
from partylinks.scrapers.tickettailor import TicketTailorSettings

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


# A host that sells this many upcoming events of a source and is not that source's own site is a store of its
# own (the cinematheque sells on cintlv.presglobal.store, Beit HaGefen's festival on haifahag.com). Fewer:
# a guide's one-off outbound links, not a store.
SELLER_HOST_MIN_EVENTS = 10


def _site_hosts(settings: SourceSettings) -> set[str]:
    """The source's own hosts: every ``*url`` setting (site_url, base_url, api_url ...)."""
    hosts = set()
    for key, value in settings:
        if key.endswith("url") and isinstance(value, str) and (h := urlsplit(value).hostname):
            hosts.add(h.removeprefix("www."))
    return hosts


def _brand(host: str) -> str:
    """eventbrite.co.uk / eventbrite.de / www.eventbrite.com -> "eventbrite": one store in many countries."""
    parts = [p for p in host.split(".") if p not in {"www", "co", "com", "org", "net", "il", "gov", "muni", "ac"}]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts else host)


def _same_town(a: str, b: str) -> bool:
    """ "תל אביב" and "תל אביב-יפו" are one town."""
    return a == b or a.startswith(b) or b.startswith(a)


def _path_offices(settings: SourceSettings) -> list[tuple[str, str, str | None]]:
    """(URL prefix, name, city) of stores told apart by their path on one host: a Ticket Tailor organizer."""
    if isinstance(settings, TicketTailorSettings):
        return [(f"{settings.site_url}/events/{o}/", o, settings.organizer_cities.get(o)) for o in settings.organizers]
    return []


def box_offices(session: Session, sources: dict[str, SourceSettings]) -> list[BoxOfficeOut]:
    """Every store inside an enabled source, most upcoming events first: the configured tenants / hosts of a
    multi-store source, a Ticket Tailor organizer, a Gray branch, and any other host selling a source's events."""
    enabled = {name: s for name, s in sources.items() if s.enabled}
    if not enabled:
        return []
    rows = session.execute(
        select(EventRow.source, EventRow.group_id, EventRow.url, EventRow.city, EventRow.organizer)
        .where(EventRow.source.in_(list(enabled)), upcoming_filter())
        .distinct()
    ).all()
    by_source: dict[str, list[tuple[Any, str, str | None, str | None]]] = defaultdict(list)
    for source, group, url, city, organizer in rows:
        by_source[source].append((group, url or "", city, organizer))
    offices: dict[str, BoxOfficeOut] = {}
    # Every source's own site is its store already: Comy's shows at Gray Club are no "grayclub.co.il" office.
    every_site = {h for s in enabled.values() for h in _site_hosts(s)}
    for source, settings in enabled.items():
        events = by_source.get(source, [])
        hosts = _hosts(settings)
        counts = Counter(h for _, h in {(g, urlsplit(u).hostname) for g, u, _, _ in events})
        for host, name, city in hosts:
            offices[host] = BoxOfficeOut(
                source=source, name=name, city=city, url=f"https://{host}", events=counts.get(host, 0)
            )
        for prefix, slug, city in _path_offices(settings):
            mine = [(g, o) for g, u, _, o in events if u.lower().startswith(prefix.lower())]
            names = Counter(o for _, o in mine if o)
            offices[prefix] = BoxOfficeOut(
                source=source,
                name=names.most_common(1)[0][0] if names else slug,
                city=city,
                url=prefix.rstrip("/"),
                events=len({g for g, _ in mine}),
            )
        if isinstance(settings, GrayclubSettings):  # one site, a branch per town: its events by their city
            for branch in settings.branches:
                offices[settings.site_url + branch.path] = BoxOfficeOut(
                    source=source,
                    name=f"Gray Club {branch.city}",
                    city=branch.city,
                    url=settings.site_url + branch.path,
                    events=len({g for g, _, c, _ in events if c and _same_town(c, branch.city)}),
                )
        own = _site_hosts(settings) | {h for h, _, _ in hosts}
        brands = {_brand(h) for h in own}
        for host, n in counts.items():
            if not host or n < SELLER_HOST_MIN_EVENTS:
                continue
            bare = host.removeprefix("www.")
            if bare in own or bare in every_site or _brand(bare) in brands or host in offices:
                continue
            cities = Counter(c for g, u, c, _ in events if c and urlsplit(u).hostname == host)
            offices[host] = BoxOfficeOut(
                source=source,
                name=bare,
                city=cities.most_common(1)[0][0] if cities else None,
                url=f"https://{host}",
                events=n,
            )
    return sorted(offices.values(), key=lambda o: (-o.events, o.name))
