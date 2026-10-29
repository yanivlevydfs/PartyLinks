"""New events x active alerts -> ``alert_hits``. Matching is ``build_event_filters`` itself, so an alert
finds exactly what the same filters show on the site.

One query per alert was 330 s a pass at production size (31k distinct searches x 500 new events), so a
pass asks the database once per *single filter value* instead (each genre, region, area, time slot, word,
date window: a few hundred small queries over the new events) and combines those sets per alert. That is
the same answer: ``build_event_filters`` ANDs one clause per filter, and every list filter is any-of its
values, so an alert's events are the intersection over its filters of the union over each list's values.
The one clause that depends on another filter, "Israel only unless searching" (``abroad``), is the base set.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from partylinks.accounts.models import UserRow
from partylinks.alerts.criteria import Criteria
from partylinks.alerts.models import AlertRow, AlertStateRow
from partylinks.config import Settings
from partylinks.db import Database, EventRow, upcoming_filter, utcnow

log = logging.getLogger(__name__)
# An event is matched once it is this old: by then its run has committed (first_seen_at is stamped before the
# commit), duplicates are regrouped and places geocoded (area / region filters need them).
# ponytail: a fixed wait; a "post-steps done" mark per run would allow less, if 30 minutes ever matters.
SETTLE = timedelta(minutes=30)
LISTS = ("genre", "region", "area", "time_slot")  # any-of: the union of each value's events
WHEN = ("weekend", "date_from", "date_to")  # one window together


class _Sets:
    """Which of the new events each single filter value lets through, each asked once per pass."""

    def __init__(self, settings: Settings, session: Session, new_ids: list[str]) -> None:
        from partylinks.api import build_event_filters  # local: api imports the alert routes

        self.build, self.settings, self.session, self.new_ids = build_event_filters, settings, session, new_ids
        self.cache: dict[tuple[Any, ...], frozenset[str]] = {}
        self.queries = 0

    def ids(self, **params: Any) -> frozenset[str]:
        key = tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in params.items()))
        if key not in self.cache:
            clauses = self.build(self.settings, **params)
            query = select(EventRow.id).where(EventRow.id.in_(self.new_ids), *clauses)
            self.cache[key] = frozenset(self.session.scalars(query))
            self.queries += 1
        return self.cache[key]

    def match(self, params: dict[str, Any]) -> frozenset[str]:
        """What ``build_event_filters(**params)`` returns among the new events."""
        # The base: Israel only, unless the alert searches a word (then everywhere, as the site does).
        found = self.ids(q=params["q"]) if "q" in params else self.ids()
        for key in LISTS:
            if values := params.get(key):
                found &= frozenset().union(*(self.ids(anywhere=True, **{key: [v]}) for v in values))
        for key in ("city", "free_entry", "discount"):
            if key in params:
                found &= self.ids(anywhere=True, **{key: params[key]})
        if window := {k: params[k] for k in WHEN if k in params}:
            found &= self.ids(anywhere=True, **window)
        return found


def _insert_hits(session: Session, rows: list[tuple[int, str, str, datetime]]) -> None:
    """COPY into a temporary table, then one INSERT ... ON CONFLICT DO NOTHING: a multi-row INSERT built
    by SQLAlchemy took 140 of 190 s for 800k matches."""
    if not rows:
        return
    session.execute(text(
        "CREATE TEMP TABLE new_hits (alert_id int, event_id text, user_id varchar(32), matched_at timestamp) ON COMMIT DROP"
    ))  # fmt: skip
    cursor = session.connection().connection.dbapi_connection.cursor()  # type: ignore[union-attr]
    with cursor.copy("COPY new_hits (alert_id, event_id, user_id, matched_at) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
    session.execute(text(
        "INSERT INTO alert_hits (alert_id, event_id, user_id, matched_at) "
        "SELECT alert_id, event_id, user_id, matched_at FROM new_hits ON CONFLICT DO NOTHING"
    ))  # fmt: skip


def run(settings: Settings, db: Database) -> int:
    """One pass over the events first seen since the last one. Returns the number of new hits."""
    now = utcnow()
    upto = now - SETTLE
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    with db.session() as session:
        state = session.get(AlertStateRow, 1)
        if state is None:  # the first pass starts now: no flood of events that were already there
            session.add(AlertStateRow(id=1, cursor=upto))
            return 0
        # New = a group whose first listing was first seen in this window (a second listing of a party
        # already announced is not news), shown by its primary listing.
        group = func.coalesce(EventRow.group_id, EventRow.id)
        new_groups = (
            select(group).group_by(group)
            .having(func.min(EventRow.first_seen_at) > state.cursor, func.min(EventRow.first_seen_at) <= upto)
        )  # fmt: skip
        new_ids = list(session.scalars(
            select(EventRow.id).where(group.in_(new_groups), EventRow.is_primary.is_(True), upcoming_filter())
        ))  # fmt: skip
        state.cursor = upto
        if not new_ids:
            return 0
        alerts = session.execute(
            select(AlertRow.id, AlertRow.user_id, AlertRow.criteria, AlertRow.criteria_hash)
            .join(UserRow, UserRow.id == AlertRow.user_id)
            .where(AlertRow.active.is_(True), UserRow.blocked_at.is_(None))
        ).all()
        owners: dict[str, list[tuple[int, str]]] = defaultdict(list)
        criteria: dict[str, Criteria] = {}
        for alert_id, user_id, raw, digest in alerts:
            owners[digest].append((alert_id, user_id))
            criteria.setdefault(digest, Criteria.model_validate(raw))
        sets = _Sets(settings, session, new_ids)
        stamp = now.replace(tzinfo=None)  # UTCDateTime columns hold naive UTC
        rows = [
            (a, event_id, u, stamp)
            for digest, who in owners.items()
            for event_id in sets.match(criteria[digest].params(today))
            for a, u in who
        ]
        _insert_hits(session, rows)
    log.info(
        "alerts: %d new events, %d alerts (%d distinct), %d queries, %d hits",
        len(new_ids), len(alerts), len(owners), sets.queries, len(rows),
    )  # fmt: skip
    return len(rows)
