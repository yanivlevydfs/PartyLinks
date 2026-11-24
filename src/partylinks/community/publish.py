"""A posted event as the ``community`` source's listing: the form becomes a ScrapedEvent, goes through the same
enrichment as every scraper's events (genres, lineup, entry type, slug, area...) and the same upsert, so the site
shows, groups and filters it like any other listing. Its pictures are already stored (images.py): their
event_images rows are marked stored at once and never downloaded. Unlisting deletes the listing like an event
that is over (db.delete_events); the form's row and its pictures stay with the account."""

from __future__ import annotations

import logging
import threading
import time
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from partylinks.community import SOURCE
from partylinks.community.models import CommunityEventRow, CommunityUploadRow
from partylinks.community.schemas import EventIn, end_at, start_at, zone
from partylinks.config import Settings
from partylinks.db import EventRow, ImageRow, delete_events, upsert_events, utcnow
from partylinks.models import ScrapedEvent, TicketTier, TierStatus

log = logging.getLogger(__name__)
FREE_TIER = {"he": "כניסה חופשית", "en": "Free entry"}
ARTISTS_TTL = 3600.0  # the artist dictionary enrichment reads is reloaded after this many seconds

_enriching = threading.Lock()  # the service's one Enricher: one event at a time in the web process
_artists_loaded = [0.0]


class DroppedError(ValueError):
    """Enrichment drops the event (enrichment.skip_titles: not a public event)."""


def event_id(row: CommunityEventRow) -> str:
    return f"{SOURCE}:{row.id}"


def picture_url(settings: Settings, upload_id: str) -> str:
    return f"{settings.site.base_url.rstrip('/')}/community/uploads/{upload_id}.webp"


def own_page(settings: Settings, row_id: int) -> str:
    """A free event with no ticket links has no seller's page: its listing points at ours."""
    return f"{settings.site.base_url.rstrip('/')}/community/events/{row_id}"


def _genre_words(settings: Settings, keys: list[str]) -> list[str]:
    """A chosen genre key as the source category enrichment reads (its first keyword: "hard techno")."""
    table = settings.enrichment.genres
    return [next(iter(table.get(k) or []), k.replace("_", " ")) for k in keys]


def _tiers(event: EventIn, tz: Any) -> list[TicketTier]:
    tiers = [
        TicketTier(
            source_ticket_id=str(i), name=t.name, price=t.price, original_price=t.original_price,
            service_fee=t.service_fee, currency=t.currency, admits=t.admits, is_free=t.is_free,
            pay_at_door=t.pay_at_door, status=TierStatus.SOLD_OUT if t.sold_out else TierStatus.ON_SALE,
            sale_end=t.sale_end.replace(tzinfo=tz) if t.sale_end and t.sale_end.tzinfo is None else t.sale_end,
        )
        for i, t in enumerate(event.tickets)
    ]  # fmt: skip
    if event.free_entry:
        tiers.append(TicketTier(source_ticket_id="free", name=FREE_TIER["he"], price=Decimal(0), is_free=True))
    return tiers


def to_scraped(row: CommunityEventRow, event: EventIn, settings: Settings) -> ScrapedEvent:
    tz = zone(event, settings.timezone)
    tiers = _tiers(event, tz)
    on_sale = [t for t in tiers if not t.is_sold_out]
    prices = [t.price for t in (on_sale or tiers) if t.price is not None]
    links = [{"name": t.name, "url": t.url} for t in event.tickets]
    return ScrapedEvent(
        source=SOURCE, source_event_id=str(row.id), url=links[0]["url"] if links else own_page(settings, row.id),
        title=event.title, description=event.description or None,
        start_at=start_at(event, tz), end_at=end_at(event, tz), time_known=event.start_time is not None,
        venue_name=event.venue_name, address=event.address or None, city=event.city,
        latitude=event.latitude, longitude=event.longitude, country=event.country,
        source_genres=_genre_words(settings, event.genres), event_types=list(event.event_types),
        artists=list(event.artists), min_age=event.min_age, organizer=event.organizer or None,
        images=[picture_url(settings, p) for p in event.pictures],
        currency=event.tickets[0].currency if event.tickets else "ILS",
        min_price=min(prices) if prices else None, max_price=max(prices) if prices else None,
        is_sold_out=bool(tiers) and not on_sale, tickets=tiers,
        extra={"community_id": row.id, "ticket_links": links, "no_ticket": not links,
               "doors_open": event.doors_open.strftime("%H:%M") if event.doors_open else None},
    )  # fmt: skip


def _enriched(service: Any, scraped: ScrapedEvent, chosen: list[str]) -> ScrapedEvent:
    with _enriching:
        if time.monotonic() - _artists_loaded[0] > ARTISTS_TTL or not _artists_loaded[0]:
            known, blocked = service.artists.load()
            featured = [p.name for p in service.artists.profiles().values()]
            service.enricher.learn_artists(known, blocked, featured=featured)
            _artists_loaded[0] = time.monotonic()
        enriched = service.enricher.enrich(scraped)
    if enriched is None:
        raise DroppedError("title: this does not read as a public event")
    enriched.genres = [*enriched.genres, *(g for g in chosen if g not in enriched.genres)]  # the user's choice holds
    return enriched


def list_event(session: Session, service: Any, settings: Settings, row: CommunityEventRow) -> str:
    """List (or update) the row's event on the site; returns its events.id."""
    event = EventIn.model_validate(row.data)
    enriched = _enriched(service, to_scraped(row, event, settings), event.genres)
    # Files dropped from the listing are the user's uploads (still theirs, claimed by community_uploads): none to delete.
    upsert_events(session, [enriched])
    uploads = {picture_url(settings, u.id): u for u in session.scalars(
        select(CommunityUploadRow).where(CommunityUploadRow.id.in_(event.pictures)))}  # fmt: skip
    for image in session.scalars(
        select(ImageRow).where(ImageRow.event_id == enriched.event_id, ImageRow.downloaded_at.is_(None))
    ):
        if upload := uploads.get(image.url):
            image.local_path, image.content_type, image.size_bytes = upload.local_path, "image/webp", upload.size_bytes
            image.downloaded_at, image.error, image.error_at = utcnow(), None, None
    row.event_id, row.status = enriched.event_id, "published"
    log.info("community event %s listed as %s", row.id, enriched.event_id)
    return enriched.event_id


def unlist(session: Session, row: CommunityEventRow, status: str | None = None) -> None:
    """Take the row's listing off the site (nothing listed: nothing to do); ``status`` is the row's new one."""
    if row.event_id:
        delete_events(session, [row.event_id])  # its files are the user's uploads: kept
        log.info("community event %s unlisted (%s)", row.id, row.event_id)
    row.event_id = None
    if status:
        row.status = status


def unlist_user(session: Session, user_id: str, status: str, reason: str) -> int:
    """Every listed event of an account off the site (the account blocked or deleted)."""
    rows = list(session.scalars(select(CommunityEventRow).where(CommunityEventRow.user_id == user_id)))
    for row in rows:
        if row.event_id or row.status in ("pending", "published"):
            unlist(session, row, status)
            row.reject_reason = reason
    return len(rows)


def forget_user(session: Session, user_id: str) -> None:
    """Before an account is deleted: its listings go (the form rows and upload rows go with the account, ON
    DELETE CASCADE). Its picture files are then claimed by no row: /community/uploads answers 404 for them at
    once, and the retention's artwork sweep (MediaStore.sweep_orphans) deletes them."""
    unlist_user(session, user_id, "removed", "account deleted")


def listing(session: Session, row: CommunityEventRow) -> EventRow | None:
    return session.get(EventRow, row.event_id) if row.event_id else None
