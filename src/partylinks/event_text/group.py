"""An event's text from the other listings of the same event, when its own source says (almost) nothing.

One event is often sold by several sites (one dedup group). The listing that fronts the event is picked
for its tickets, not its text: Smarticket's copy of a Herzliya talk has no description while the city's
own page has the whole lecture. Read time, like the pictures (``api._group_covers``): nothing is copied,
and a source that later writes its own text shows it at once.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from partylinks.db import EventRow
from partylinks.event_text.config import EventTextSettings


def group_description(session: Session, row: EventRow, cfg: EventTextSettings) -> str | None:
    """``row``'s own description, or the longest of its group when its own is under ``group_text_min_chars``."""
    own = (row.description or "").strip()
    if not row.group_id or len(own) >= cfg.group_text_min_chars:
        return row.description
    best = session.scalar(
        select(EventRow.description)
        .where(EventRow.group_id == row.group_id, EventRow.id != row.id, EventRow.description.is_not(None))
        .order_by(func.length(EventRow.description).desc(), EventRow.id)
        .limit(1)
    )
    return best if best and len(best.strip()) > len(own) else row.description
