"""``events.is_sponsored``: which events are paid right now, in the event table itself.

Paid = pinned to the home rows (``home_pins``) or a sponsored placement (``sponsored``), whose ``until`` has
not passed. ``sync`` sets the column to that; called after every pin / sponsor change and on every regroup
(so a campaign's end clears it within a scrape cycle). Default false: an event is not paid until picked.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_, select, union, update
from sqlalchemy.orm import Session

from partylinks.ads.models import SponsoredRow
from partylinks.db import EventRow
from partylinks.home_pins.models import HomePinRow


def sync(session: Session) -> None:
    """Two small updates: the newly paid set true, the no longer paid set false; nothing for rows already right.
    ponytail: the second scans events for is_sponsored (no index); a partial index if it ever shows up slow."""
    now = datetime.now(UTC)
    paid = union(
        select(HomePinRow.event_id).where(or_(HomePinRow.until.is_(None), HomePinRow.until > now)),
        select(SponsoredRow.event_id).where(or_(SponsoredRow.until.is_(None), SponsoredRow.until > now)),
    ).scalar_subquery()
    session.execute(
        update(EventRow).where(EventRow.id.in_(paid), EventRow.is_sponsored.is_(False)).values(is_sponsored=True),
        execution_options={"synchronize_session": False},
    )
    session.execute(
        update(EventRow).where(EventRow.is_sponsored.is_(True), EventRow.id.not_in(paid)).values(is_sponsored=False),
        execution_options={"synchronize_session": False},
    )
