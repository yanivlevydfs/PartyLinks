"""EASYTIX (easytix.co.il) - the ticketing platform of nimi (live.nimi.co.il), white-labelled for its
marketplaces: easytix itself, Camel Comedy Club (comedyclub.co.il), Hormesis, ZOA House, the Jerusalem
Biblical Zoo and Israel Aquarium, and others. Every page is a React app fed by one JSON API:

* ``/user/marketplace/getMarketplaceIdByDomainRegex?domain=<domain>`` - a marketplace's id;
* ``/event/public-events/marketplace/<id>`` - every public event of that marketplace (no paging);
* ``/event/public-events/featured?limit=50`` - the easytix home page rail, across marketplaces;
* ``/event/ticket/getTickets/<account id>/<event id>`` - the ticket types with prices and stock.

An event carries its title, HTML description, poster (``design.image``), free-text ``location``
("venue, street, city"), the day (``start_date``) and the time (``start_hour``), both UTC instants.
Disabled, password, members-only and test events are skipped, and so are season passes and admission
tickets that run for months (``max_span_days``). robots.txt allows everything.

An organiser can keep an event out of every listing (``isPublic: false``) and sell it through a link
it posts (BLACKOUT HALLOWEEN at Gagarin, Nov 2026). The event pages other sources link to
(``linked_urls``) are read too: ``/user/account/getByHandler/<account>`` gives the account,
``/event/getEventByHandler/<account id>/<event>`` the event. A published link makes it public;
``event_pages`` adds links found elsewhere (an organiser's Facebook ad).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, parse_iso, to_decimal, zone
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
    featured_limit: int = 50
    marketplaces: list[Marketplace] = Field(default_factory=list)
    # Longer than this from first to last day: a season pass or an admission ticket, not an event.
    max_span_days: int = 31
    # Accounts (handlers) that sell no events: test accounts, punch cards.
    skip_accounts: list[str] = Field(default_factory=list)
    # Event pages read even when no listing has them: unlisted events organisers sell through a posted link.
    event_pages: list[str] = Field(default_factory=list)


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
                e = {**e, "account": account, "isPublic": True}
                found[e["_id"]] = (e, by_id.get(_marketplace_id(e) or ""))
        self.log.debug("%d event pages linked from other sources and not listed", len(pages))
        now = datetime.now().astimezone()
        events: list[ScrapedEvent] = []
        for event_id, (e, m) in found.items():
            try:
                ev = parse_event(e, m, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd event never drops the others
                self.record_failure(f"event {event_id}", exc)
                continue
            if ev and _upcoming(ev, now):
                events.append(ev)
        self.log.debug("%d events listed, %d coming and public", len(found), len(events))

        async def with_tickets(event: ScrapedEvent) -> ScrapedEvent:
            tickets = await self.optional(
                f"tickets of {event.source_event_id}",
                self.get_json(
                    f"{api}/event/ticket/getTickets/{event.extra['account_id']}/{event.source_event_id}", expect=list
                ),
            )
            return add_tickets(event, tickets or [], now)

        async for event in self.stream_isolated(events, with_tickets, lambda e: e.source_event_id):
            yield event


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def linked_pages(urls: list[str], hosts: set[str]) -> list[tuple[str, str]]:
    """(account, event) handlers of the event pages among ``urls`` on these box offices, once each."""
    found = (_PAGE.match(urlsplit(u).path) for u in urls if _host(u) in hosts)
    return list(dict.fromkeys((m.group(1), m.group(2)) for m in found if m))


def _marketplace_id(event: dict[str, Any]) -> str | None:
    market = event.get("marketplace")
    return market.get("_id") if isinstance(market, dict) else market if isinstance(market, str) else None


def _upcoming(event: ScrapedEvent, now: datetime) -> bool:
    last = event.end_at or (event.start_at + timedelta(days=1) if event.start_at else None)
    return last is not None and last > now


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
        extra={"account_id": account["_id"], "marketplace": market.domain if market else None},
    )


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
