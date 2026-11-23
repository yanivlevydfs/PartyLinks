"""EASYTIX (easytix.co.il) - the ticketing platform of nimi (live.nimi.co.il), white-labelled for its
marketplaces: easytix itself, Camel Comedy Club (comedyclub.co.il), Hormesis, ZOA House, the Jerusalem
Biblical Zoo and Israel Aquarium, and others. Every page is a React app fed by one JSON API:

* ``/user/marketplace/getMarketplaceIdByDomainRegex?domain=<domain>`` - a marketplace's id;
* ``/event/public-events/marketplace/<id>`` - every public event of that marketplace (no paging);
* ``/event/public-events/featured?limit=1000`` - the easytix home page rail, across marketplaces, whole
  (the home page shows 16; 57 on 23.11.2026, so 50 cut it);
* ``/event/ticket/getTickets/<account id>/<event id>`` - the ticket types with prices and stock.

An event carries its title, HTML description, poster (``design.image``), free-text ``location``
("venue, street, city"), the day (``start_date``) and the time (``start_hour``), both UTC instants.
Disabled, password, members-only and test events are skipped, and so are season passes and admission
tickets that run for months (``max_span_days``). robots.txt allows everything.

A run of dated shows sells as one event with a time-slot calendar (``timeSlotConfig``): its own dates
(``dateOverrides``, each with its first and last slot) and no weekly days. Each open date is one event,
from its first slot to the end of its last, no end after one short arrival slot (Penguin People ALIVE at
Lost & Found: the opening night and 15 evenings, Dec 2026; the Israel Aquarium's sleepover tours), the
date of ``start_date`` under the event's id. A calendar that opens every week is an admission ticket or a
weekly class: read as the one event (and so skipped when it runs for months).

An organiser can keep an event out of every listing (``isPublic: false``) and sell it through a link
it posts (BLACKOUT HALLOWEEN at Gagarin, Nov 2026). The event pages other sources link to
(``linked_urls``) are read too: ``/user/account/getByHandler/<account>`` gives the account,
``/event/getEventByHandler/<account id>/<event>`` the event. A published link makes it public;
``event_pages`` adds links found elsewhere (an organiser's Facebook ad).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, parse_iso, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

# nimi's event ``type`` -> words the enrichment reads as site genres. "electronic-party" is the form's
# default (Camel's stand-up nights carry it), so it says nothing; a marketplace's own genres say more.
_PAGE = re.compile(r"^/event/([^/?#]+)/([^/?#]+)/?$")
TYPES = {"wellness": "wellness", "music": "הופעות", "culture": "תרבות", "education": "הרצאות"}


class Marketplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    city: str | None = None  # where its venues are when the location names none
    genres: list[str] = Field(default_factory=list)  # what every event of it is (comedyclub: stand-up)


class EasytixSettings(SourceSettings):
    site_url: str = "https://easytix.co.il"
    api_url: str = "https://live.nimi.co.il"
    featured_limit: int = 1000  # the whole rail: the API answers this many when it has them
    marketplaces: list[Marketplace] = Field(default_factory=list)
    # Longer than this from first to last day: a season pass or an admission ticket, not an event.
    max_span_days: int = 31
    # Accounts (handlers) that sell no events: test accounts, punch cards.
    skip_accounts: list[str] = Field(default_factory=list)
    # Event pages read even when no listing has them: unlisted events organisers sell through a posted link.
    event_pages: list[str] = Field(default_factory=list)
    # The town of a venue a location names alone ("מועדון Lost & Found"): venue keyword (any case) -> town.
    venue_cities: dict[str, str] = Field(default_factory=dict)


class EasytixScraper(BaseScraper[EasytixSettings]):
    name = "easytix"
    settings_model = EasytixSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s, api = self.settings, self.settings.api_url
        found: dict[str, tuple[dict[str, Any], Marketplace | None]] = {}
        by_id: dict[str, Marketplace] = {}
        for market in s.marketplaces:
            mid = await self.optional(
                f"marketplace {market.domain}",
                self.get_json(
                    f"{api}/user/marketplace/getMarketplaceIdByDomainRegex",
                    params={"domain": market.domain},
                    expect=str,
                ),
            )
            if not mid:
                continue
            by_id[mid] = market
            listed = await self.optional(
                f"events of {market.domain}",
                self.get_json(
                    f"{api}/event/public-events/marketplace/{mid}", params={"timezone": self.timezone}, expect=list
                ),
            )
            for e in listed or []:
                if isinstance(e, dict) and e.get("_id"):
                    found[e["_id"]] = (e, market)
        featured = await self.optional(
            "featured",
            self.get_json(
                f"{api}/event/public-events/featured",
                params={"timezone": self.timezone, "limit": s.featured_limit},
                expect=list,
            ),
        )
        for e in featured or []:
            if isinstance(e, dict) and e.get("_id") and e["_id"] not in found:
                found[e["_id"]] = (e, by_id.get(_marketplace_id(e) or ""))
        hosts = {_host(s.site_url), *(_host("https://" + m.domain) for m in s.marketplaces)}
        listed_pages = {
            (str((e.get("account") or {}).get("handler")), str(e.get("handler"))) for e, _ in found.values()
        }
        pages = [p for p in linked_pages([*s.event_pages, *self.linked_urls], hosts) if p not in listed_pages]
        accounts: dict[str, dict[str, Any] | None] = {}
        for acc, handler in pages:
            if acc not in accounts:
                accounts[acc] = await self.optional(
                    f"account {acc}", self.get_json(f"{api}/user/account/getByHandler/{acc}", expect=dict)
                )
            account = accounts[acc]
            if not account or not account.get("_id"):
                continue
            e = await self.optional(
                f"linked event {acc}/{handler}",
                self.get_json(f"{api}/event/getEventByHandler/{account['_id']}/{handler}", expect=dict),
            )
            if e and e.get("_id") and e["_id"] not in found:
                # Unlisted (isPublic false) but sold through the link someone published: public.
                linked: dict[str, Any] = {**e, "account": account, "isPublic": True}
                found[str(e["_id"])] = (linked, by_id.get(_marketplace_id(linked) or ""))
        self.log.debug("%d event pages linked from other sources and not listed", len(pages))
        now = datetime.now().astimezone()
        events: list[ScrapedEvent] = []
        for event_id, (e, m) in found.items():
            try:
                dated = parse_events(e, m, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd event never drops the others
                self.record_failure(f"event {event_id}", exc)
                continue
            events.extend(
                ev for ev in dated
                if is_current(ev.start_at, ev.end_at, RetentionSettings(), self.timezone, time_known=ev.time_known)
            )  # fmt: skip
        self.log.debug("%d events listed, %d coming and public dates", len(found), len(events))
        # One ticket request per nimi event, shared by its dates.
        asked: dict[str, asyncio.Task[list[Any] | None]] = {}

        async def tickets_of(account_id: str, event_id: str) -> list[Any] | None:
            return await self.optional(
                f"tickets of {event_id}",
                self.get_json(f"{api}/event/ticket/getTickets/{account_id}/{event_id}", expect=list),
            )

        async def with_tickets(event: ScrapedEvent) -> ScrapedEvent:
            eid = event.extra["event_id"]
            if eid not in asked:
                asked[eid] = asyncio.ensure_future(tickets_of(event.extra["account_id"], eid))
            return add_tickets(event, await asked[eid] or [], now)

        # Marketplaces list theirs one after another: the ticket requests (and saves) go soonest first,
        # so a cut run holds the nearest week (Nov 2026).
        async for event in self.stream_isolated(soonest_first(events), with_tickets, lambda e: e.source_event_id):
            yield event


def soonest_first(events: list[ScrapedEvent]) -> list[ScrapedEvent]:
    """Events by start time, undated last: stream_isolated fetches in input order."""
    last = datetime.max.replace(tzinfo=UTC)
    return sorted(events, key=lambda e: e.start_at or last)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def linked_pages(urls: list[str], hosts: set[str]) -> list[tuple[str, str]]:
    """(account, event) handlers of the event pages among ``urls`` on these box offices, once each."""
    found = (_PAGE.match(urlsplit(u).path) for u in urls if _host(u) in hosts)
    return list(dict.fromkeys((m.group(1), m.group(2)) for m in found if m))


def _marketplace_id(event: dict[str, Any]) -> str | None:
    market = event.get("marketplace")
    return market.get("_id") if isinstance(market, dict) else market if isinstance(market, str) else None


def _at(day: Any, clock: Any, tz: str) -> tuple[datetime | None, bool]:
    """The local day of ``day`` at the local time of ``clock`` (both UTC instants); (midnight, False)
    without a time. nimi keeps the two apart: the day alone is stored at noon UTC."""
    local = zone(tz)
    date_at, clock_at = parse_iso(day), parse_iso(clock)
    if date_at is None:
        return (clock_at.astimezone(local), True) if clock_at else (None, False)
    d = date_at.astimezone(local).date()
    if clock_at is None:
        return datetime.combine(d, time(), local), False
    return datetime.combine(d, clock_at.astimezone(local).time().replace(second=0, microsecond=0), local), True


def _unescape(text: Any) -> str:
    r"""The site stores some quotes escaped: "אלנבי 128א\'", "סופ\"ש"."""
    return str(text or "").replace("\\'", "'").replace('\\"', '"')


def split_location(location: str | None, default_city: str | None) -> tuple[str | None, str | None, str | None]:
    """ "קאמל קומדי קלאב, אלנבי 128א\\', תל-אביב" -> (venue, street, city); one part is the venue."""
    parts = [p for p in (clean_str(x) for x in _unescape(location).split(",")) if p]
    if not parts:
        return None, None, default_city
    if len(parts) == 1:
        return parts[0], None, default_city
    return parts[0], ", ".join(parts[1:-1]) or None, parts[-1]


def parse_event(e: dict[str, Any], market: Marketplace | None, s: EasytixSettings, tz: str) -> ScrapedEvent | None:
    """One public event of the API, or None when it is not one people can buy a ticket to."""
    account = e.get("account") if isinstance(e.get("account"), dict) else {}
    title, handler, acc = (
        clean_str(_unescape(e.get("name"))),
        clean_str(e.get("handler")),
        clean_str(account.get("handler")),
    )
    if (
        not title or not handler or not acc or not account.get("_id")
        or not e.get("is_enabled") or e.get("isPublic") is False
        or e.get("is_password_enabled") or e.get("isOnlyForMembers") or acc in s.skip_accounts
    ):  # fmt: skip
        return None
    start, time_known = _at(e.get("start_date"), e.get("start_hour"), tz)
    if start is None:
        return None
    end = None
    if e.get("end_hour"):
        end, _ = _at(e.get("end_date") or e.get("start_date"), e.get("end_hour"), tz)
    elif e.get("end_date"):
        end_day, _ = _at(e.get("end_date"), None, tz)
        end = end_day + timedelta(days=1) if end_day else None  # the whole last day
    if end is not None and end <= start:
        end = end + timedelta(days=1) if time_known and end.date() == start.date() else None
    if end is not None and (end - start).days > s.max_span_days:
        return None
    venue, address, city = split_location(e.get("location"), market.city if market else None)
    if city is None and venue:
        city = next((town for key, town in s.venue_cities.items() if key.casefold() in venue.casefold()), None)
    genres = list(market.genres) if market else []
    if genre := TYPES.get(e.get("type") or ""):
        genres.append(genre)
    image = (e.get("design") or {}).get("image") if isinstance(e.get("design"), dict) else None
    return ScrapedEvent(
        source=EasytixScraper.name,
        source_event_id=str(e["_id"]),
        url=f"{s.site_url.rstrip('/')}/event/{acc}/{handler}",
        title=title,
        description=html_to_text(e.get("description")),
        start_at=start,
        end_at=end,
        time_known=time_known,
        venue_name=venue,
        address=address,
        city=city,
        source_genres=list(dict.fromkeys(genres)),
        organizer=clean_str(account.get("name")),
        images=[image] if isinstance(image, str) and image.startswith("https://") else [],
        extra={
            "account_id": account["_id"],
            "event_id": str(e["_id"]),
            "marketplace": market.domain if market else None,
        },
    )


def _clock(hhmm: Any) -> time | None:
    """nimi's slot clock "1700" -> 17:00."""
    text = str(hhmm or "")
    if len(text) != 4 or not text.isdigit() or int(text[:2]) > 23 or int(text[2:]) > 59:
        return None
    return time(int(text[:2]), int(text[2:]))


def slot_days(e: dict[str, Any], tz: str) -> list[tuple[date, time, time | None]]:
    """The days a time-slot event opens on dates of its own (``dateOverrides``), each with its first and
    last slot, as the event page's calendar shows them; [] when it has none, or opens every week."""
    cfg = e.get("timeSlotConfig")
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return []
    weekly = cfg.get("daySchedules")
    if isinstance(weekly, dict) and any(isinstance(d, dict) and d.get("enabled") for d in weekly.values()):
        return []  # an admission ticket or a weekly class, not a run of dates
    local = zone(tz)
    first, last = parse_iso(e.get("start_date")), parse_iso(e.get("end_date"))
    days: dict[date, tuple[time, time | None]] = {}
    for o in cfg.get("dateOverrides") or []:
        sched = o.get("schedule") if isinstance(o, dict) else None
        if not isinstance(sched, dict) or sched.get("enabled") is False:  # the site: unset is open
            continue
        try:
            day = date.fromisoformat(str(o.get("date")))
        except ValueError:
            continue
        start = _clock(sched.get("startTime"))
        if start is None or (first and day < first.astimezone(local).date()):
            continue
        if last and day > last.astimezone(local).date():
            continue
        days[day] = (start, _clock(sched.get("endTime")))
    return [(day, start, end) for day, (start, end) in sorted(days.items())]


def parse_events(e: dict[str, Any], market: Marketplace | None, s: EasytixSettings, tz: str) -> list[ScrapedEvent]:
    """Every date of one API event: each open day of a run of dated shows, else the event itself."""
    days = slot_days(e, tz)
    if not days:
        event = parse_event(e, market, s, tz)
        return [event] if event else []
    base = parse_event({**e, "end_date": None, "end_hour": None}, market, s, tz)  # each day is its own span
    if base is None or base.start_at is None:
        return []
    local, first = zone(tz), base.start_at.date()
    events = []
    for day, opens, closes in days:
        start = datetime.combine(day, opens, local)
        end = datetime.combine(day, closes, local) if closes else None
        if end is not None and end <= start:
            end += timedelta(days=1)  # "2100"-"0100": past midnight
        if end is not None and end - start < timedelta(hours=1):
            end = None  # one short arrival slot ("1730"-"1800" for a night at the aquarium), not the end
        events.append(base.model_copy(update={
            "source_event_id": base.source_event_id if day == first else f"{base.source_event_id}:{day.isoformat()}",
            "start_at": start,
            "end_at": end,
            "time_known": True,
        }))  # fmt: skip
    return events


def add_tickets(event: ScrapedEvent, tickets: list[Any], now: datetime) -> ScrapedEvent:
    """Ticket types on sale (``is_enabled`` is the string "true") -> tiers, price range, sold out."""
    tiers: list[TicketTier] = []
    for t in tickets:
        if not isinstance(t, dict) or str(t.get("is_enabled")).lower() != "true" or not clean_str(t.get("name")):
            continue
        sale_end = parse_iso(t.get("end_date"))
        if sale_end is not None and sale_end < now:
            continue
        price = to_decimal(t.get("price"))
        tiers.append(TicketTier(
            source_ticket_id=clean_str(t.get("_id")),
            name=clean_str(t.get("name")) or "",
            price=price,
            currency=clean_str(t.get("currency")) or "ILS",
            is_free=price == 0,
            is_sold_out=bool(t.get("is_sold_out")),
            remaining=t.get("ticketsLeft") if isinstance(t.get("ticketsLeft"), int) else None,
            sale_end=sale_end,
        ))  # fmt: skip
    prices = [t.price for t in tiers if t.price is not None and t.price > Decimal(0)]
    if prices:  # ponytail: a 0 tier beside paid ones is a wheelchair / escort seat, not free entry
        tiers = [t for t in tiers if not t.is_free]
    return event.model_copy(update={
        "tickets": tiers,
        "min_price": min(prices) if prices else None,
        "max_price": max(prices) if prices else None,
        "is_sold_out": bool(tiers) and all(t.is_sold_out for t in tiers),
    })  # fmt: skip
