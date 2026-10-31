"""Eventim Light (eventim-light.com): CTS Eventim's self-service box office. Each organizer has a shop
per market (``/<country>/a/<organizerId>/``: de, at, ch, uk, nl, it, es, dk, se...); there is no public
catalogue, search or sitemap across shops.

Discovery: the Common Crawl URL index (index.commoncrawl.org, a public index of crawled URLs) lists the
shop and event URLs it has seen; every ``/<country>/a/<id>`` in it is a shop. Known shops are kept in
``known_file`` (on the data volume) and grow run by run; each Common Crawl collection is read once.
Details: one request per shop. Its server-rendered page carries the shop's upcoming events as JSON
(``<script id="vike_pageContext">``, ``data``): title, start/end with offset, category, teaser, artwork,
lowest price, sold out, and the venue with its address, country and coordinates. Cancelled and past
events are skipped. Israeli venues go to Israel, the rest abroad (the venue's country).

Access: Akamai refuses Python's TLS handshake and shops pass through a Queue-it check; speaking as
Chrome (``http.impersonate``, curl_cffi) passes both. A shop first answers with a small cookie-test page
(``document.location.href = decodeURIComponent('...')``) on the same host, followed once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx
from pydantic import Field
from selectolax.lexbor import LexborHTMLParser
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from partylinks.config import project_path
from partylinks.countries import country_code
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, jsonld_nodes, parse_iso, schema_types, to_decimal, to_float
from partylinks.scrapers.base import BaseScraper, SourceSettings

_COOKIE_REDIRECT = re.compile(r"document\.location\.href\s*=\s*decodeURIComponent\('([^']+)'\)")
_CANCELLED = ("cancel", "abgesagt")


class EventimLightSettings(SourceSettings):
    site_url: str
    # A shop's page and an event's page, from the shop key "<country>/<organizerId>".
    shop_path: str = "/{country}/a/{shop}/"
    event_path: str = "/{country}/a/{shop}/e/{event}"
    image_path: str = "/{country}/api/image/{image}/facebook_event_cover/webp"
    # Market paths of the site; a discovered shop under any other path is ignored.
    countries: list[str] = Field(min_length=1)
    # Shops always checked, besides the discovered ones ("de/6395bc712f27c4432c8bda66").
    seed_shops: list[str] = Field(default_factory=list)
    # Common Crawl URL index: the newest `cdx_collections` crawls, each read once.
    cdx_url: str = "https://index.commoncrawl.org"
    cdx_collections: int = Field(12, ge=0)
    cdx_timeout_seconds: float = Field(300, gt=0)
    cdx_attempts: int = Field(4, ge=1)
    # The index is slow and often answers 502/504: discovery gets this long per run.
    cdx_budget_seconds: float = Field(1800, gt=0)
    # Known shops and the crawls already read (a JSON file on the data volume).
    known_file: Path = Path("data/eventimlight_shops.json")
    # Safety cap on shops fetched per run (not a volume limit).
    max_shops: int = Field(100_000, ge=1)
    # Eventim Light's category names (lower case) as the category words the enrichment knows.
    categories: dict[str, list[str]] = Field(default_factory=dict)


class EventimLightScraper(BaseScraper[EventimLightSettings]):
    name = "eventimlight"
    settings_model = EventimLightSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        known = await asyncio.to_thread(load_known, self.settings.known_file)
        try:
            if self.settings.cdx_collections:
                # Crawls left unread by the budget are read by the next runs.
                async with asyncio.timeout(self.settings.cdx_budget_seconds):
                    await self._discover(known)
        except Exception as exc:  # noqa: BLE001 - the index is down: the known shops still run
            self.log.warning("Common Crawl discovery failed: %s %s", type(exc).__name__, exc)
        shops = sorted({*self.settings.seed_shops, *known["shops"]})[: self.settings.max_shops]
        self.log.info("%d shops to check (%d crawls read)", len(shops), len(known["collections"]))
        gone: set[str] = set()

        async def fetch(shop: str) -> list[ScrapedEvent]:
            try:
                page = await self.shop_page(shop)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (404, 410):
                    gone.add(shop)  # closed shop: dropped from the known set
                    return []
                raise
            return parse_shop(page, shop, self.settings)

        # One shop per market first, alone: it takes the Queue-it pass (a cookie) that lets the market's
        # other shops answer at once. Started in parallel, every shop queued its own pass behind the rest.
        first = list({shop.partition("/")[0]: shop for shop in reversed(shops)}.values())
        batches = [[shop] for shop in first] + [sorted(set(shops) - set(first))]
        try:
            for batch in batches:
                async for events in self.stream_isolated(batch, fetch, label=lambda s: s):
                    for event in events:
                        yield event
        finally:
            known["shops"] = sorted(set(known["shops"]) - gone)
            save_known(self.settings.known_file, known)  # direct: on shutdown the thread pool is closed
        self.log.info("%d of %d shops closed", len(gone), len(shops))

    async def shop_page(self, shop: str) -> str:
        """A shop's page, past its cookie-test redirect."""
        country, _, org = shop.partition("/")
        url = self.settings.site_url + self.settings.shop_path.format(country=country, shop=org)
        response = await self.request("GET", url)
        if target := cookie_redirect(response.text):
            response = await self.request("GET", urljoin(str(response.url), target))
        return response.text

    async def _discover(self, known: dict[str, list[str]]) -> None:
        """Add the shops of the newest Common Crawl collections not read yet."""
        s = self.settings
        async with httpx.AsyncClient(timeout=s.cdx_timeout_seconds, follow_redirects=True) as client:

            async def get(endpoint: str, params: dict[str, str] | None = None) -> httpx.Response:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(s.cdx_attempts),
                    wait=wait_exponential(multiplier=5, max=60),
                    retry=retry_if_exception(lambda e: isinstance(e, httpx.TransportError | httpx.HTTPStatusError)),
                    reraise=True,
                ):
                    with attempt:
                        response = await client.get(endpoint, params=params)
                        if response.status_code != 404:  # 404: nothing of this host in that crawl
                            response.raise_for_status()
                        return response
                raise AssertionError("unreachable")

            collections = [c["id"] for c in (await get(f"{s.cdx_url}/collinfo.json")).json()][: s.cdx_collections]
            for coll in (c for c in collections if c not in known["collections"]):
                index = f"{s.cdx_url}/{coll}-index"
                query = {"url": _host(s.site_url), "matchType": "domain"}
                try:
                    pages = int((await get(index, {**query, "showNumPages": "true"})).json().get("pages", 1))
                    lines: list[str] = []
                    for page in range(pages):
                        response = await get(index, {**query, "output": "json", "fl": "url", "page": str(page)})
                        lines += response.text.splitlines() if response.status_code == 200 else []
                except (httpx.HTTPError, ValueError) as exc:
                    self.log.warning(
                        "Common Crawl %s not read (next run tries again): %s %s", coll, type(exc).__name__, exc
                    )
                    continue
                found = shops_in_index(lines, _host(s.site_url), s.countries)
                new = found - set(known["shops"])
                known["shops"] = sorted({*known["shops"], *found})
                known["collections"].append(coll)
                self.log.info("Common Crawl %s: %d URLs, %d shops, %d new", coll, len(lines), len(found), len(new))
                save_known(s.known_file, known)


def cookie_redirect(page_html: str) -> str | None:
    """The target of the shop's cookie-test page (a few KB), or None for a real page."""
    match = _COOKIE_REDIRECT.search(page_html) if len(page_html) < 20_000 else None
    return unquote(match.group(1)) if match else None


def _host(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def shops_in_index(lines: list[str], host: str, countries: list[str]) -> set[str]:
    """Shop keys "<country>/<organizerId>" in Common Crawl index lines (JSON, one URL each)."""
    markets = "|".join(map(re.escape, countries))
    pattern = re.compile(re.escape(host) + r"/(" + markets + r")/a/([0-9a-f]{24})(?:[/?#]|$)")
    shops = set()
    for line in lines:
        try:
            url = json.loads(line)["url"]
        except (ValueError, KeyError, TypeError):
            continue
        if match := pattern.search(url):
            shops.add(f"{match.group(1)}/{match.group(2)}")
    return shops


def parse_shop(
    page_html: str, shop: str, settings: EventimLightSettings, now: datetime | None = None
) -> list[ScrapedEvent]:
    """A shop page's upcoming events (not cancelled, not over)."""
    node = LexborHTMLParser(page_html).css_first("script#vike_pageContext")
    if node is None:
        raise ParseError(f"{shop}: no vike_pageContext on the shop page")
    context = json.loads(node.text())
    items = context.get("data")
    if not isinstance(items, list):
        items = ((context.get("initialStoreState") or {}).get("events") or {}).get("eventOverviewItems") or []
    cancelled = {
        clean_str(n.get("url"))
        for n in jsonld_nodes(page_html)
        if "event" in schema_types(n) and "cancel" in str(n.get("eventStatus") or "").lower()
    }
    country, _, org = shop.partition("/")
    now = now or datetime.now(UTC)
    events = []
    for item in items:
        if not isinstance(item, dict) or not item.get("id") or not clean_str(item.get("title")):
            continue
        url = settings.site_url + settings.event_path.format(country=country, shop=org, event=item["id"])
        status = str(item.get("status") or "").lower()
        if url in cancelled or any(word in status for word in _CANCELLED):
            continue
        start, end = parse_iso(item.get("start")), parse_iso(item.get("end"))
        if start is None:
            continue
        end = end if end and end > start else None  # Eventim Light gives end = start when unset
        if (end or start) < now:  # never past events
            continue
        venue = item.get("venue") if isinstance(item.get("venue"), dict) else {}
        geo = venue.get("geo") if isinstance(venue.get("geo"), dict) else {}
        venue_country = country_code(clean_str(venue.get("country")) or country)  # "UK" -> GB
        price = item.get("minPrice") if isinstance(item.get("minPrice"), dict) else {}
        image_id = (item.get("image") or {}).get("id") if isinstance(item.get("image"), dict) else None
        image = settings.site_url + settings.image_path.format(country=country, image=image_id) if image_id else None
        category = clean_str(item.get("category"))
        street = clean_str(venue.get("street"))
        city = clean_str(venue.get("city"))
        place = " ".join(p for p in (clean_str(venue.get("zipCode")), city) if p)
        events.append(
            ScrapedEvent(
                source=EventimLightScraper.name,
                source_event_id=str(item["id"]),
                url=url,
                title=clean_str(item["title"]) or "",
                description=clean_str(item.get("teaser")) or clean_str(item.get("subtitle")),
                start_at=start,
                end_at=end,
                venue_name=clean_str(venue.get("name")),
                address=", ".join(p for p in (street, place, venue_country) if p) or None,
                city=city,
                latitude=to_float(geo.get("latitude")),
                longitude=to_float(geo.get("longitude")),
                country=None if venue_country == "IL" else venue_country,
                source_genres=[c.casefold() for c in [category] if c]
                + settings.categories.get((category or "").casefold(), []),
                organizer=clean_str((item.get("affiliate") or {}).get("name"))
                if isinstance(item.get("affiliate"), dict)
                else None,
                image_url=image,
                images=[image] if image else [],
                currency=clean_str(price.get("currency")) or "EUR",
                min_price=to_decimal(price.get("value")),
                is_sold_out=bool(item.get("soldout")),
                extra={"category": category, "shop": shop},
            )
        )
    return events


def load_known(path: Path) -> dict[str, list[str]]:
    try:
        data = json.loads(project_path(path).read_text("utf-8"))
        return {"shops": list(data.get("shops", [])), "collections": list(data.get("collections", []))}
    except (OSError, ValueError, AttributeError):
        return {"shops": [], "collections": []}


def save_known(path: Path, known: dict[str, list[str]]) -> None:
    target = project_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_suffix(".tmp").write_text(json.dumps(known), "utf-8")
        target.with_suffix(".tmp").replace(target)
    except OSError as exc:
        logging.getLogger(__name__).warning("eventimlight known shops not saved: %s", exc)
