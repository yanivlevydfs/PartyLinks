"""Issta (issta.co.il) - packages (flight + hotel + ticket) to concerts abroad.

The tickets page (``/tickets``) lists every package as a schema.org Product card: name, a short
description, the destination airport and departure date (in the link), the outbound and return
days (dd/mm) and a "from" price. The show's own date is only on the package page, which
robots.txt disallows (``/loader*``, ``/sport/details*``), so an event spans the trip: departure
to return. Packages of the same trip (same name, airport and dates) are one event, cheapest to
dearest. The destination city and country come from the airport code (airportsdata).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from selectolax.parser import HTMLParser, Node

from partylinks.enrichment import UNKNOWN_COUNTRY
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, to_decimal, zone
from partylinks.scrapers.abroad import airports
from partylinks.scrapers.base import BaseScraper, SourceSettings

CURRENCY_SIGNS = {"€": "EUR", "£": "GBP", "₤": "GBP", "$": "USD", "₪": "ILS"}
# IATA city codes that are no airport of their own (airportsdata lists airports only).
METRO_CODES = {"LON": ("London", "GB"), "PAR": ("Paris", "FR"), "MIL": ("Milan", "IT"), "ROM": ("Rome", "IT"),
               "NYC": ("New York", "US"), "STO": ("Stockholm", "SE"), "BER": ("Berlin", "DE")}  # fmt: skip


class IsstaSettings(SourceSettings):
    site_url: str
    listing_path: str


class IsstaScraper(BaseScraper[IsstaSettings]):
    name = "issta"
    settings_model = IsstaSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        page = await self.get_text(s.site_url + s.listing_path)
        events = parse_listing(page, s.site_url, self.timezone)
        self.log.info("listed %d events", len(events))
        for event in events:
            yield event


def destination(code: str) -> tuple[str | None, str]:
    """(city, ISO country) of an IATA airport or city code."""
    if code in METRO_CODES:
        return METRO_CODES[code]
    airport = airports().get(code)
    return (airport["city"], airport["country"]) if airport else (None, UNKNOWN_COUNTRY)


def _on_or_after(day_month: str, after: date) -> date:
    """dd/mm as the first such day on or after ``after``."""
    day, month = (int(p) for p in day_month.split("/"))
    found = date(after.year, month, day)
    return found if found >= after else date(after.year + 1, month, day)


def _card(node: Node, site_url: str) -> tuple[tuple[str, str, date, date], str, dict[str, object]] | None:
    """(trip key, package id, fields) of one card; None for a card that is no dated package."""
    link = node.css_first("a[itemprop=url]")
    name = node.css_first("[itemprop=name]")
    href = link.attributes.get("href") if link else None
    title = clean_str(name.text()) if name else None
    if not href or not title:
        return None
    # /loader?url=/sport/details?sid=..&pid=..: the inner URL's "&" split it into the outer query.
    inner = parse_qs(urlsplit(href).query)
    pid, airport, fdate = (inner.get(k, [""])[0] for k in ("pid", "dport", "fdate"))
    days = [s.text(strip=True) for s in node.css(".directions span") if "/" in s.text()]
    if not pid or not fdate or len(days) != 2:
        return None
    booked = datetime.strptime(fdate, "%d/%m/%Y").date()
    # The card's own days win: the link's date is sometimes another departure of the package.
    out = _on_or_after(days[0], booked - timedelta(days=180))
    back = _on_or_after(days[1], out)
    price = node.css_first("[itemprop=price]")
    sign = node.css_first("[itemprop=priceCurrency]")
    description = node.css_first("[itemprop=description]")
    image = node.css_first("img[itemprop=image]")
    fields: dict[str, object] = {
        "url": site_url + href,
        "title": title,
        "description": clean_str(description.text()) if description else None,
        "image_url": image.attributes.get("src") if image else None,
        "price": to_decimal(price.attributes.get("content")) if price else None,
        "currency": CURRENCY_SIGNS.get((sign.attributes.get("content") or "").strip(), "EUR") if sign else "EUR",
        "airport": airport,
    }
    return (title, airport, out, back), pid, fields


def parse_listing(page_html: str, site_url: str, timezone: str) -> list[ScrapedEvent]:
    """One event per trip (name, destination, dates); its packages give the price range."""
    trips: dict[tuple[str, str, date, date], list[tuple[str, dict[str, object]]]] = {}
    for node in HTMLParser(page_html).css("div.deal-item-container"):
        card = _card(node, site_url)
        if card:
            key, pid, fields = card
            trips.setdefault(key, []).append((pid, fields))
    if not trips and "deal-item-container" in page_html:
        raise ParseError("package cards found but none parsed (layout changed?)")
    tz = zone(timezone)
    events = []
    for (title, airport, out, back), packages in trips.items():
        packages.sort(key=lambda p: (p[1]["price"] is None, p[1]["price"] or 0, p[0]))
        pid, first = packages[0]
        prices = [p for _, f in packages if isinstance(p := f["price"], Decimal)]
        city, country = destination(airport)
        events.append(
            ScrapedEvent(
                source=IsstaScraper.name,
                source_event_id=pid,
                url=str(first["url"]),
                title=title,
                description=f"חבילה: טיסה, מלון וכרטיס. {first['description'] or ''}".strip(),
                start_at=datetime(out.year, out.month, out.day, tzinfo=tz),  # trip dates, not the show's
                end_at=datetime(back.year, back.month, back.day, tzinfo=tz),
                city=city,
                country=country,
                image_url=first["image_url"],
                currency=str(first["currency"]),
                min_price=min(prices, default=None),
                max_price=max(prices, default=None),
                extra={"package": True, "airport": airport, "packages": [p for p, _ in packages]},
            )
        )
    return events
