"""Short event links: ``https://spotx.fun/e/<code>``, the one canonical address of an event page.

A code is 8 characters made by Sqids (``sqids``, the successor of Hashids) over a fixed 27-letter
alphabet: digits 2-9 and lowercase consonants, so nothing a reader can mistake (no 0/o, 1/l/i), one case
(a link typed from a poster or read out works), nothing a chat app percent-encodes or cuts off, and no
vowels; Sqids' own blocklist drops the rest of what could still spell a word.

The number encoded is the first 4 bytes of sha256 of the listing's id (``source:source_event_id``), so
the same event always gets the same code, even when it is deleted after its date and scraped again
(the integer primary key would not do: a re-scraped event is a new row). 2^32 numbers always fit 8
characters (Sqids' 8 hold 26^7, 8 billion): 7 would hold only 26^6 (309 million) and clash ~13 times as
often. The code is stored in ``events.code`` (unique index) so a link is looked up by index; on the rare
clash with another event's code (about 1 new listing in 20,000 at 200k stored) the next candidate of the
same id is taken (``id#1``, ``id#2``...), and once stored a code never changes.

``/event/<long slug>`` (the old address) answers 301 to the short one; ``/e/<code>`` of a duplicate
listing answers 301 to its group's primary listing, so each real event has one address (see seo.register).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING

from sqids import Sqids
from sqlalchemy import String as SAString
from sqlalchemy import Table, column, select, values
from sqlalchemy import Text as SAText
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from partylinks.db import Database

log = logging.getLogger(__name__)

# "23456789bcdfghjkmnpqrstvwxz" shuffled once (Sqids takes the order as its key). Never change it or
# LENGTH: stored codes stay, but a re-scraped event would get a new code and its old links would break.
ALPHABET = "xf6zmvtcnqbjsp57hkwg9234d8r"
LENGTH = 8
_SQIDS = Sqids(alphabet=ALPHABET, min_length=LENGTH)  # the default blocklist
_VALID = re.compile(f"^[{ALPHABET}]{{{LENGTH},{LENGTH + 2}}}$")  # a blocked number is re-encoded, rarely longer
# What a link may carry after the code: a cosmetic tail ("/e/x7k2m9qd/party"), punctuation a chat app
# glued on ("x7k2m9qd)."), upper case. Only the code is read.
_LEADING = re.compile(r"^[A-Za-z0-9]+")


def candidate(event_id: str, attempt: int = 0) -> str:
    """The ``attempt``-th code of a listing id (0: its own; later ones only after a clash)."""
    seed = event_id if attempt == 0 else f"{event_id}#{attempt}"
    return _SQIDS.encode([int.from_bytes(hashlib.sha256(seed.encode()).digest()[:4], "big")])


def normalize(text: str) -> str | None:
    """The code a link names, or None when it names none."""
    found = _LEADING.match(text.strip())
    code = found.group(0).lower() if found else ""
    return code if _VALID.match(code) else None


def path(code: str) -> str:
    return f"/e/{code}"


def _table(session: Session) -> Table:
    from partylinks.db import EventRow  # local: db imports this module

    return EventRow.__table__  # type: ignore[return-value]


def assign(session: Session, event_ids: Iterable[str]) -> dict[str, str]:
    """A free code for each of these listings (ids without a stored code): its own candidate, else the next
    one not taken by another event nor by another id of this batch."""
    ids = sorted(set(event_ids))
    if not ids:
        return {}
    table = _table(session)
    attempt = dict.fromkeys(ids, 0)
    chosen: dict[str, str] = {}
    pending = ids
    while pending:
        wanted = {i: candidate(i, attempt[i]) for i in pending}
        taken = dict(
            session.execute(select(table.c.code, table.c.id).where(table.c.code.in_(set(wanted.values())))).all()
        )
        used = set(chosen.values())
        pending = []
        for event_id, code in wanted.items():
            if taken.get(code, event_id) != event_id or code in used:
                attempt[event_id] += 1
                pending.append(event_id)
            else:
                chosen[event_id] = code
                used.add(code)
    return chosen


def backfill(db: Database, batch: int = 2000, max_batches: int = 1000, pause: float = 0.5) -> int:
    """Codes for stored listings that have none (rows from before ``events.code``), a batch per
    transaction so no lock is held long, with a ``pause`` between them so scrapes write in between.
    Returns how many were written."""
    written = 0
    for _ in range(max_batches):
        try:
            with db.session() as session:
                table = _table(session)
                ids = list(
                    session.scalars(select(table.c.id).where(table.c.code.is_(None)).order_by(table.c.id).limit(batch))
                )
                if not ids:
                    break
                codes = assign(session, ids)
                rows = values(column("id", SAText), column("code", SAString), name="new_codes").data(
                    list(codes.items())
                )
                session.execute(
                    table.update().where(table.c.id == rows.c.id, table.c.code.is_(None)).values(code=rows.c.code)
                )
                written += len(codes)
            time.sleep(pause)
        except IntegrityError:  # a scrape stored one of these codes meanwhile: the next pass picks again
            log.info("shortlinks: a code was taken meanwhile, retrying the batch")
            continue
    if written:
        log.info("shortlinks: %d events got a short code", written)
    return written
