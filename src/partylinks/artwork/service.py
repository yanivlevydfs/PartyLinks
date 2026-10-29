"""Which events get a drawn card, drawing them concurrently, and taking them away again.

Rules (owner's): the scrapers bring the pictures; a card is drawn only for an event none of whose listings
(its duplicate group) has a picture stored or still waiting to download, and a real picture always wins:
once one is stored, the card is deleted. Every card shows the event's name; retention deletes it with its event.

A pass, in three steps:
1. One short read transaction takes a plain snapshot of the events that need a (new) card, and another
   deletes the cards no longer needed. No database object crosses into the drawing threads.
2. Cards are drawn concurrently: asyncio tasks, at most ``CONCURRENCY`` at a time, each in a thread
   (Pillow's blur, resize and WebP encoding run outside the GIL). No transaction is open while drawing.
3. Each finished card is saved in its own transaction, which first checks the event still has no picture
   (one may have arrived while drawing), then writes both sizes and the signature in one upsert. The card's
   bytes live only in that row, so the card and the database succeed or fail together: no file to leave
   behind, no row without its picture.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from partylinks.artwork import draw
from partylinks.artwork.models import EventArtworkRow
from partylinks.config import Settings
from partylinks.db import Database, EventRow, ImageRow, upcoming_filter, utcnow

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

log = logging.getLogger(__name__)
PER_PASS = 300  # ~0.3 s of CPU each: the first pass after a deploy spreads ~700 over a few runs
# ponytail: threads, bounded by the worker's CPUs (Pillow's heavy steps release the GIL); a process pool
# if drawing ever becomes the worker's bottleneck.
CONCURRENCY = max(1, min(4, os.cpu_count() or 1))


@dataclass(frozen=True)
class Job:
    """What one card shows: a plain copy of the event, safe to hand to a thread."""

    event_id: str
    title: str
    sources: tuple[str, ...]
    start_at: datetime | None
    city: str | None
    time_known: bool
    signature: str


def _has_picture() -> Any:
    """Some listing of this event's group has a picture stored, or one its scraper found still downloading."""
    listing = aliased(EventRow)
    return exists().where(
        ImageRow.event_id == listing.id,
        listing.group_id == EventRow.group_id,  # every event has one (its own id when alone): indexed
        or_(ImageRow.downloaded_at.is_not(None), ImageRow.error.is_(None)),
    )


def needing(session: Session) -> list[EventRow]:
    """Upcoming events (one per group, the primary listing) with no picture at all."""
    return list(session.scalars(
        select(EventRow).where(EventRow.is_primary.is_(True), upcoming_filter(), ~_has_picture())
    ))  # fmt: skip


def _signature(row: EventRow, sources: list[str]) -> str:
    """What the card shows: a change to any of it redraws the card."""
    names = draw.store_names()
    parts = [row.title or "", ",".join(names.get(s, s) for s in sources), row.start_at.isoformat() if row.start_at else "",
             row.city or "", str(row.time_known)]  # fmt: skip
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def plan(db: Database) -> tuple[list[Job], int, int]:
    """Step 1: the cards to draw (new or outdated, up to PER_PASS), how many events need one, and how many
    cards were removed (a real picture arrived, the event ended or is no longer its group's primary)."""
    from partylinks.api import _group_sources  # local: api imports this package's routes

    with db.session() as session:
        rows = needing(session)
        wanted = {r.id for r in rows}
        stale = delete(EventArtworkRow).where(EventArtworkRow.event_id.not_in(wanted))
        removed = cast("CursorResult[Any]", session.execute(stale)).rowcount or 0
    with db.session(write=False) as session:
        have = {e: sig for e, sig in session.execute(select(EventArtworkRow.event_id, EventArtworkRow.signature))}
        sources = _group_sources(session, rows)
    jobs = []
    for r in rows:
        srcs = sources.get(r.id) or [r.source]
        sig = _signature(r, srcs)
        if have.get(r.id) != sig:
            jobs.append(Job(r.id, r.title or "", tuple(srcs), r.start_at, r.city, r.time_known is not False, sig))
    return jobs[:PER_PASS], len(wanted), removed


def make(settings: Settings, db: Database, job: Job) -> bool:
    """Steps 2 and 3 for one card (run in a thread): draw it, then save it in one transaction, only if
    the event still has no picture. True when saved."""
    img = draw.draw(event_id=job.event_id, title=job.title, sources=list(job.sources), start_at=job.start_at,
                    city=job.city, time_known=job.time_known, timezone=settings.timezone)  # fmt: skip
    big, small = draw.webp(img, 1280), draw.webp(img, 640)
    with db.session() as session:  # one transaction: the check and the write commit together, or neither
        still_bare = session.scalar(
            select(EventRow.id).where(EventRow.id == job.event_id, EventRow.is_primary.is_(True), ~_has_picture())
        )
        if still_bare is None:  # a real picture came while drawing (or the event went): it wins
            return False
        values = {"signature": job.signature, "webp_1280": big, "webp_640": small, "made_at": utcnow()}
        session.execute(
            insert(EventArtworkRow).values(event_id=job.event_id, **values)
            .on_conflict_do_update(index_elements=[EventArtworkRow.event_id], set_=values)
        )  # fmt: skip
    return True


async def run_async(settings: Settings, db: Database) -> dict[str, int]:
    """One pass, cards drawn ``CONCURRENCY`` at a time."""
    jobs, needed, removed = await asyncio.to_thread(plan, db)
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(job: Job) -> bool:
        async with gate:
            try:
                return await asyncio.to_thread(make, settings, db, job)
            except Exception:  # one card's failure never stops the others; its event is tried next pass
                log.exception("artwork for %s failed", job.event_id)
                return False

    drawn = sum(await asyncio.gather(*(one(j) for j in jobs)))
    log.info("artwork: %d events without a picture, %d drawn, %d removed", needed, drawn, removed)
    return {"needing": needed, "drawn": drawn, "removed": removed}


def run(settings: Settings, db: Database) -> dict[str, int]:
    """One pass, from synchronous code (tests, scripts)."""
    return asyncio.run(run_async(settings, db))


def urls(session: Session, event_ids: list[str]) -> dict[str, str]:
    """Event id -> its drawn card's address (/artwork/{id}), for events that have one."""
    if not event_ids:
        return {}
    found = session.execute(
        select(EventArtworkRow.event_id, EventArtworkRow.id).where(EventArtworkRow.event_id.in_(event_ids))
    )
    return {event_id: f"/artwork/{card_id}" for event_id, card_id in found}
