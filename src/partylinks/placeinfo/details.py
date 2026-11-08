"""One place's details, merged. Hours: the place's own site first, then HappyTLV's, then Ontopo's."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from partylinks.db import EventRow
from partylinks.placeinfo.config import PlaceInfoSettings
from partylinks.placeinfo.models import OntopoPhotoRow, OntopoVenueRow, PlaceInfoRow


def _https(site: str) -> str:
    return site if "://" in site else f"https://{site}"


def place_details(session: Session, row: EventRow, cfg: PlaceInfoSettings, lang: str) -> dict[str, Any]:
    """The place page's data for one listing; keys with nothing to say are left out."""
    extra = row.extra or {}
    info = session.get(PlaceInfoRow, row.url)
    venue = session.get(OntopoVenueRow, info.ontopo_slug) if info and info.ontopo_slug else None
    site = (info.site if info else None) or {}
    hours, hours_from = next(
        ((h, name) for h, name in ((site.get("hours"), "site"), (extra.get("hours"), "happytlv"), (venue and venue.hours, "ontopo")) if h),
        (None, None),
    )
    links = [*(extra.get("links") or []), *([extra["website"]] if extra.get("website") else [])]
    own_site = next((u for u in links if not any(h in urlsplit(u).netloc for h in cfg.social_hosts)), None)
    photos = (
        session.scalars(select(OntopoPhotoRow.n).where(OntopoPhotoRow.slug == venue.slug).order_by(OntopoPhotoRow.n)).all()
        if venue else []
    )  # fmt: skip
    found = {
        "hours": hours,
        "hours_from": hours_from,
        "deal_hours": extra.get("deal_hours"),
        "deal_note": extra.get("deal_note"),
        "phone": extra.get("phone") or site.get("phone") or (venue and venue.phone),
        "address": row.address or site.get("address") or (venue and venue.address),
        "website": own_site or (_https(venue.website) if venue and venue.website else None),
        "instagram": next((u for u in links if "instagram.com" in u), None),
        "rating": extra.get("rating"),
        "reviews": extra.get("reviews"),
        "price_level": extra.get("price_level"),
        "maps_url": extra.get("maps_url"),
        "menu_url": extra.get("menu_url"),
        "hood": extra.get("hood"),
        "cuisine": extra.get("cuisine") or (venue and venue.tags),
        "amenities": extra.get("amenities"),
        "tags": [t for t in extra.get("tags") or [] if t in cfg.tags],
        "ontopo_url": cfg.ontopo_booking.format(lang=lang, slug=venue.slug) if venue else None,
        "booking_url": extra.get("booking_url"),
        "photos": [f"/place-photos/{venue.slug}/{n}.webp" for n in photos] if venue else [],
    }
    return {k: v for k, v in found.items() if v}
