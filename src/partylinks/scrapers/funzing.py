"""Funzing (il.funzing.com) - experiences all over Israel: workshops, tours, lectures, culinary evenings,
shows, family days and outdoor trips, each run by its host on one or more dates.

The site's pages are an Angular app fed by a JSON API (``api-il.funzing.com/api/v2``):
* ``/funz/search.json?all=true&page=N`` - every experience, 10 a page, those with coming dates first
  (``number_of_upcoming_dates``); about 30 pages have dates, the other ~400 none, so the listing stops at
  the first page without one;
* ``/funz/<id>`` - the experience: text, ``category_slug``, ``funz_interests`` (keys), the map pin
  (``latitude`` / ``longitude``), ``displayed_address``, adult / kids price ("₪100") and its ``events``
  (``date_start`` with the zone, ``end_time`` "20:00", ``sold_out``);
* ``/categories`` - each category's interests, key -> Hebrew name.
Each date is one event; online experiences are skipped.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text
from partylinks.scrapers.base import BaseScraper, SourceSettings

_PRICE = re.compile(r"\d+(?:\.\d+)?")
_CLOUDINARY_SIZE = re.compile(r"(/image/upload/)[a-z]{1,2}_[^/]+/")
# category_slug -> words the enrichment reads as site genres.
CATEGORIES = {
    "workshops": "סדנאות", "tours": "סיורים", "independent-tours": "סיורים", "lectures": "הרצאות",
    "food-and-drink": "אוכל", "family-and-kids": "ילדים ומשפחה", "outdoors": "טבע", "night-out": "מופעים",
    "wellness": "wellness",
}  # fmt: skip
# Interests whose Hebrew name reads as another genre: "אלטרנטיבי" is alternative medicine, not rock; music,
# dance, films and shows are the subject of a workshop or a lecture (night-out events are shows by category).
INTERESTS = {"alternative": "wellness", "music_and_dance": "", "films_and_shows": "קולנוע"}

ONLINE = {"אונליין", "online", "Online", "זום"}


class FunzingSettings(SourceSettings):
    api_url: str = "https://api-il.funzing.com/api/v2"
    max_pages: int = 100


class FunzingScraper(BaseScraper[FunzingSettings]):
    name = "funzing"
    settings_model = FunzingSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        api = self.settings.api_url
        interests = parse_interests(await self.optional("categories", self.get_json(f"{api}/categories")) or {})
        ids: list[int] = []
        for page in range(1, self.settings.max_pages + 1):
            found = (await self.get_json(f"{api}/funz/search.json", params={"all": "true", "page": page})).get("funzes") or []
            dated = [f["id"] for f in found if isinstance(f, dict) and f.get("number_of_upcoming_dates") and f.get("id")]
            ids.extend(dated)
            if not dated:
                break
        self.log.info("listed %d experiences with dates", len(ids))
        now = datetime.now().astimezone()

        async def detail(funz_id: int) -> list[ScrapedEvent]:
            funz = (await self.get_json(f"{api}/funz/{funz_id}")).get("funz") or {}
            return [e for e in parse_funz(funz, interests) if e.start_at and e.start_at > now]

        async for events in self.stream_isolated(dict.fromkeys(ids), detail, lambda funz_id: f"funz {funz_id}"):
            for event in events:
                yield event


def parse_interests(categories: dict[str, Any]) -> dict[str, str]:
    """``/categories`` -> {interest key: Hebrew name}."""
    return {
        i["key"]: i["display_name"]
        for box in categories.get("category_boxes") or []
        for i in box.get("interests") or []
        if isinstance(i, dict) and i.get("key") and i.get("display_name")
    }


def parse_funz(funz: dict[str, Any], interests: dict[str, str]) -> list[ScrapedEvent]:
    """``/funz/<id>`` -> one event per coming date; none for an online experience (city "אונליין")."""
    funz_id, title = funz.get("id"), clean_str(funz.get("title"))
    if not funz_id or not title or funz.get("online") or (funz.get("city") or "").strip() in ONLINE:
        return []
    category = funz.get("category_slug") or ""
    keys = [k.strip() for k in (funz.get("funz_interests") or "").split(",") if k.strip()]
    genres = [g for g in (CATEGORIES.get(category), clean_str(funz.get("mobile_display_name"))) if g]
    genres += [INTERESTS.get(k, interests.get(k, k.replace("_", " "))) for k in keys]
    if funz.get("intended_for_children") or funz.get("funz_on_kids_category"):  # for_kids: kids may join
        genres.append("ילדים ומשפחה")
    prices = [p for p in (_price(funz.get("adult_price")), _price(funz.get("kids_price"))) if p]
    city = clean_str(funz.get("city"))
    address = clean_str(funz.get("displayed_address"))
    cover = funz.get("cover_url")
    owner = funz.get("owner") if isinstance(funz.get("owner"), dict) else {}
    text = "\n\n".join(t for part in (funz.get("description"), funz.get("what_you_get")) if part and (t := html_to_text(part)))
    url = funz.get("canonical_url") or f"https://il.funzing.com/funz/{funz.get('slug') or funz_id}"
    events: list[ScrapedEvent] = []
    for e in funz.get("events") or []:
        try:
            start = datetime.fromisoformat(e["date_start"])
        except (KeyError, TypeError, ValueError):
            continue
        end = None
        if m := re.fullmatch(r"(\d{1,2}):(\d{2})", e.get("end_time") or ""):
            end = start.replace(hour=int(m[1]), minute=int(m[2]))
            end = end + timedelta(days=1) if end <= start else end
        events.append(ScrapedEvent(
            source=FunzingScraper.name,
            source_event_id=str(e.get("id") or f"{funz_id}-{start:%Y%m%d%H%M}"),
            url=url,
            title=title,
            description=text or None,
            start_at=start,
            end_at=end,
            address=address if address and address != city else None,
            city=city,
            latitude=funz.get("latitude"),
            longitude=funz.get("longitude"),
            source_genres=list(dict.fromkeys(genres)),
            organizer=clean_str(owner.get("first_name") or owner.get("name")),
            images=[_CLOUDINARY_SIZE.sub(r"\1", cover)] if isinstance(cover, str) and cover.startswith("https://") else [],
            min_price=min(prices) if prices else None,
            max_price=max(prices) if prices else None,
            is_sold_out=bool(e.get("sold_out")),
            extra={"funz_id": funz_id},
        ))  # fmt: skip
    return events


def _price(value: object) -> Decimal | None:
    """"₪100" -> 100; "₪0" or nothing -> None."""
    m = _PRICE.search(str(value or "").replace(",", ""))
    return Decimal(m.group(0)) if m and Decimal(m.group(0)) > 0 else None
