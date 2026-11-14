"""inperson (inperson.social): a Tel Aviv social-meetup app (tables of six, matchmaking evenings,
yoga, cocktail workshops) whose hosts approve every sign-up.

robots.txt is a 404 (nothing disallowed) and there is no sitemap. Listing: the ``/events`` page is
rendered on the server with a schema.org ``ItemList`` JSON-LD naming every coming event (~5).
Details: each ``/events/<slug>`` page carries one ``SocialEvent`` JSON-LD block (dates, place, organizer
community, priced offers), parsed with the generic schema.org parser shared with Chillz.

What the site lacks: no coordinates and often no venue (the dating tables say "revealed later": only the
city is kept, geocoded in that city); some ends are a day after the start (dropped). Events are
age-gated and by invitation (``extra.invite_only``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, jsonld_nodes, schema_types
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.chillz import parse_page


class InpersonSettings(SourceSettings):
    site_url: str = "https://inperson.social"
    list_path: str = "/events"
    source_genres: list[str] = Field(default_factory=lambda: ["פעילות חברתית"])
    # The organizer community (/com/<slug>) names the kind of evening: dating tables, workshops, yoga.
    community_genres: dict[str, list[str]] = Field(default_factory=dict)
    # A place name that only says the venue is told to the approved guests later.
    hidden_venue_words: list[str] = Field(default_factory=lambda: ["revealed later", "נחשף", "יפורסם", "יימסר"])
    max_hours: int = Field(12, ge=1)  # an end further than this after the start is a placeholder


class InpersonScraper(BaseScraper[InpersonSettings]):
    name = "inperson"
    settings_model = InpersonSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        urls = parse_listing(await self.get_text(self.settings.site_url + self.settings.list_path))
        self.log.debug("listed %d events", len(urls))
        async for event in self.stream_isolated(urls, self._fetch_event, label=lambda u: u):
            if event and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                yield event

    async def _fetch_event(self, url: str) -> ScrapedEvent:
        return parse_event(await self.get_text(url), url, self.settings, self.timezone)


def parse_listing(page_html: str) -> list[str]:
    """Event page URLs of the listing's ItemList JSON-LD, in order, once each."""
    urls = [
        item["url"]
        for node in jsonld_nodes(page_html)
        if "itemlist" in schema_types(node)
        for item in node.get("itemListElement") or []
        if isinstance(item, dict) and item.get("url")
    ]
    return list(dict.fromkeys(urls))


def parse_event(page_html: str, url: str, settings: InpersonSettings, default_tz: str) -> ScrapedEvent:
    event = parse_page(page_html, url, default_tz)
    if event is None:
        raise ParseError(f"{url}: no schema.org event on the page")
    data = event.model_dump()
    venue = (event.venue_name or "").casefold()
    if any(w in venue for w in settings.hidden_venue_words):
        data["venue_name"] = None
    if event.end_at and event.start_at and event.end_at - event.start_at > timedelta(hours=settings.max_hours):
        data["end_at"] = None
    community = next(
        (
            str(o.get("url") or "").rstrip("/").rsplit("/com/", 1)[-1]
            for n in jsonld_nodes(page_html)
            if (o := n.get("organizer")) and isinstance(o, dict) and "/com/" in str(o.get("url"))
        ),
        None,
    )
    data.update(
        source=InpersonScraper.name,
        source_genres=[*settings.source_genres, *settings.community_genres.get(community or "", [])],
        artist_profiles=[{**p, "source": InpersonScraper.name} for p in data["artist_profiles"]],
        extra={**event.extra, "invite_only": True, "community": community},
    )
    return ScrapedEvent.model_validate(data)
