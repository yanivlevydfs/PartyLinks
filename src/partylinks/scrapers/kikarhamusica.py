"""Kikar HaMusica (kikar-hamusica.com/shows/he), Jerusalem's music square - daily live sets at its stage.

WordPress + The Events Calendar, so the generic Tribe source reads it: the JSON API under
``/shows/he/wp-json/tribe/events/v1/events`` lists every upcoming show (~90 over two months)
with local start/end, artwork and a music genre. No price, seller link or venue is filled in
(the shows are on no ticket site), so venue and address come from the config. robots.txt
allows everything.

Entry is free: the site's home page labels this calendar "הופעות חינם" (free shows; its paid
auditorium concerts are a separate page outside the feed), and the feed's ``cost`` is always
empty, so a show without a price gets a price-0 tier. Saturdays carry a placeholder entry
("שבת", description "אין הופעות" = no shows), which is not an event and is dropped.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.scrapers.tribe import TribeScraper

_NO_SHOWS = "אין הופעות"


class KikarHamusicaScraper(TribeScraper):
    name = "kikarhamusica"

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        async for event in super().scrape():
            if event := free_entry(event):
                yield event


def free_entry(event: ScrapedEvent) -> ScrapedEvent | None:
    """The stage's shows are free; None for the "no shows" Saturday placeholder."""
    if (event.description or "").strip() == _NO_SHOWS:
        return None
    if not event.tickets:
        event.tickets = [TicketTier(name="כניסה חופשית", price=Decimal(0), is_free=True)]
        event.min_price = event.max_price = Decimal(0)
    return event
