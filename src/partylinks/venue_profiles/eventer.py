"""Eventer's venue catalog: one request for the list, one per venue for its page, one per logo."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from partylinks.parsing import clean_str, to_float
from partylinks.venue_profiles import VenueProfile

if TYPE_CHECKING:
    from partylinks.scrapers.eventer import EventerScraper

_LINKS = ("webSiteLink", "instagramLink", "facebookLink")


async def eventer_venues(scraper: EventerScraper) -> list[VenueProfile]:
    s = scraper.settings
    catalog = await scraper.get_json(s.base_url + s.venues_path, expect=dict)
    out = []
    for slide in catalog.get("slides") or []:
        if not (isinstance(slide, dict) and slide.get("linkName") and slide.get("title")):
            continue
        link = str(slide["linkName"])
        page = await scraper.optional(f"venue {link}", scraper.get_json(s.base_url + s.venue_path.format(link_name=quote(link)), expect=dict))
        if page is not None and page.get("isDeleted"):
            continue
        profile = parse_venue(slide, page or {}, s.base_url + s.venue_page_path.format(link_name=quote(link)))
        if profile.logo_url:
            response = await scraper.optional(f"venue logo {link}", scraper.request("GET", profile.logo_url))
            profile.logo = response.content if response is not None else None
        out.append(profile)
    return out


def parse_venue(slide: dict[str, Any], page: dict[str, Any], page_url: str) -> VenueProfile:
    name = clean_str(page.get("name")) or str(slide["title"]).strip()
    images = page.get("images") or slide.get("images") or {}
    location = page.get("location") or {}
    return VenueProfile(
        key=f"eventer-{page.get('_id') or (slide.get('objectRef') or {}).get('_id') or slide['linkName']}",  # = events' VENUE_PROFILE
        name=name,
        source="eventer",
        aliases=[a for part in name.split("|") if (a := clean_str(part))],
        about=clean_str(page.get("desc")),
        address=clean_str(page.get("locationDescription")),
        latitude=to_float(location.get("latitude")),
        longitude=to_float(location.get("longitude")),
        links=[u for k in _LINKS if isinstance(u := page.get(k), str) and u.startswith("http")],
        profile_url=page_url,
        # The wide picture: the site's own card and venue banner.
        logo_url=clean_str(images.get("imageDefault") or images.get("imageSquare")),
    )
