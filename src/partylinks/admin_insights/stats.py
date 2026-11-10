"""The counts behind the admin's "features" and "quality" tabs. One aggregate query per table, on the
read-only session: ~10 queries for features, one grouped query for quality."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import Text, case, cast, distinct, func, select
from sqlalchemy.orm import Session

from partylinks.accounts.models import UserRow
from partylinks.alerts.models import AlertHitRow, AlertRow, AlertStateRow
from partylinks.artwork.models import EventArtworkRow
from partylinks.db import DealChangeRow, EventRow, upcoming_filter, utcnow
from partylinks.passkeys.models import PasskeyRow
from partylinks.placeinfo.models import OntopoVenueRow, PlaceInfoRow
from partylinks.plans.models import PlanRow, PlanVoteRow
from partylinks.popularity.models import EventViewRow
from partylinks.saved.models import SavedEventRow


def features(session: Session, days: int = 7) -> dict[str, Any]:
    now = utcnow()
    since = now - timedelta(days=days)
    week = func.count().filter  # count(*) FILTER (WHERE ...)

    alerts = session.execute(
        select(
            func.count(),
            week(AlertRow.active.is_(True)),
            week(AlertRow.active.is_(True) & AlertRow.instant.is_(True)),
            week(AlertRow.created_at >= since),
        ).select_from(AlertRow)
    ).one()
    hits = session.execute(
        select(
            week(AlertHitRow.matched_at >= since),
            week(AlertHitRow.instant_sent_at >= since),
            week(AlertHitRow.digest_sent_at >= since),
        ).select_from(AlertHitRow)
    ).one()
    cursor = session.scalar(select(AlertStateRow.cursor).where(AlertStateRow.id == 1))

    saves = session.execute(
        select(func.count(), week(SavedEventRow.saved_at >= since), func.count(distinct(SavedEventRow.user_id)))
    ).one()
    most_saved = session.execute(
        select(EventRow.title, EventRow.slug, func.count().label("n"))
        .select_from(SavedEventRow)
        .join(EventRow, EventRow.id == SavedEventRow.event_id)
        .where(upcoming_filter())
        .group_by(EventRow.title, EventRow.slug)
        .order_by(func.count().desc())
        .limit(10)
    ).all()

    today = now.date()
    views_per_day = session.execute(
        select(EventViewRow.day, func.count(), func.count(distinct(EventViewRow.visitor)))
        .where(EventViewRow.day >= today - timedelta(days=days))
        .group_by(EventViewRow.day)
        .order_by(EventViewRow.day)
    ).all()
    most_viewed = session.execute(
        select(EventRow.title, EventRow.slug, func.count().label("n"))
        .select_from(EventViewRow)
        .join(EventRow, EventRow.id == EventViewRow.event_id)
        .where(EventViewRow.day >= today - timedelta(days=days), upcoming_filter())
        .group_by(EventRow.title, EventRow.slug)
        .order_by(func.count().desc())
        .limit(10)
    ).all()

    plans = session.execute(select(func.count(), week(PlanRow.created_at >= since)).select_from(PlanRow)).one()
    votes = dict(session.execute(select(PlanVoteRow.answer, func.count()).group_by(PlanVoteRow.answer)).all())
    rides = session.scalar(select(func.count()).where(PlanVoteRow.ride.is_not(None))) or 0

    users = session.execute(
        select(
            func.count(),
            week(UserRow.google_sub.is_not(None)),
            week(UserRow.facebook_id.is_not(None)),
            week(UserRow.created_at >= since),
            week(UserRow.last_login_at >= since),
            week(UserRow.blocked_at.is_not(None)),
        ).select_from(UserRow)
    ).one()
    passkeys = session.execute(select(func.count(), func.count(distinct(PasskeyRow.user_id)))).one()

    artwork = session.execute(
        select(
            func.count(),
            week(EventArtworkRow.made_at >= since),
            func.max(EventArtworkRow.made_at),
            func.coalesce(func.sum(func.length(EventArtworkRow.webp_640) + func.length(EventArtworkRow.webp_1280)), 0),
        ).select_from(EventArtworkRow)
    ).one()
    no_picture = session.scalar(
        select(func.count()).where(upcoming_filter(), EventRow.is_primary.is_(True), EventRow.image_url.is_(None))
    )

    ontopo = session.execute(select(func.count(), func.max(OntopoVenueRow.fetched_at))).one()
    places = session.execute(
        select(
            func.count(), week(PlaceInfoRow.ontopo_slug.is_not(None)), week(PlaceInfoRow.site.is_not(None))
        ).select_from(PlaceInfoRow)
    ).one()

    deals = session.execute(
        select(DealChangeRow.kind, func.count()).where(DealChangeRow.at >= since).group_by(DealChangeRow.kind)
    ).all()
    deals_by_source = session.execute(
        select(DealChangeRow.source, func.count())
        .where(DealChangeRow.at >= since)
        .group_by(DealChangeRow.source)
        .order_by(func.count().desc())
        .limit(15)
    ).all()

    top = lambda rows: [{"title": t, "slug": s, "n": n} for t, s, n in rows]
    return {
        "days": days,
        "alerts": {
            "total": alerts[0], "active": alerts[1], "instant": alerts[2], "new": alerts[3],
            "hits": hits[0], "instant_sent": hits[1], "digest_sent": hits[2],
            # How far behind the matcher is: events stored after it are not matched yet.
            "cursor": cursor, "cursor_lag_minutes": round((now - cursor).total_seconds() / 60) if cursor else None,
        },
        "saves": {"total": saves[0], "new": saves[1], "users": saves[2], "top": top(most_saved)},
        "views": {
            "per_day": [{"day": d, "views": v, "visitors": u} for d, v, u in views_per_day],
            "top": top(most_viewed),
        },
        "plans": {"total": plans[0], "new": plans[1], "votes": votes, "rides": rides},
        "users": {
            "total": users[0], "google": users[1], "facebook": users[2], "new": users[3],
            "active": users[4], "blocked": users[5], "passkeys": passkeys[0], "passkey_users": passkeys[1],
        },
        "artwork": {
            "drawn": artwork[0], "new": artwork[1], "last": artwork[2], "bytes": artwork[3],
            "upcoming_without_picture": no_picture or 0,
        },
        "place_info": {
            "ontopo_venues": ontopo[0], "ontopo_last": ontopo[1],
            "places": places[0], "matched_ontopo": places[1], "with_site": places[2],
        },
        "deals": {"by_kind": dict(deals), "by_source": dict(deals_by_source)},
    }  # fmt: skip


def quality(session: Session) -> list[dict[str, Any]]:
    """Per source, of its upcoming listings: how many lack a place on the map, a genre, a venue, a city,
    a picture, a price, an area. A share that jumps is a scraper that drifted."""
    missing = lambda cond: func.sum(case((cond, 1), else_=0))
    empty_json = ("[]", "null")
    rows = session.execute(
        select(
            EventRow.source,
            func.count(),
            missing(EventRow.latitude.is_(None)),
            missing(cast(EventRow.genres, Text).in_(empty_json) | EventRow.genres.is_(None)),
            missing(func.coalesce(EventRow.venue_name, "") == ""),
            missing(func.coalesce(EventRow.city, "") == ""),
            missing(EventRow.image_url.is_(None)),
            missing(EventRow.min_price.is_(None) & EventRow.has_free_entry.is_not(True)),
            missing(EventRow.area.is_(None)),
        )
        .where(upcoming_filter())
        .group_by(EventRow.source)
        .order_by(func.count().desc())
    ).all()
    keys = ("map", "genre", "venue", "city", "picture", "price", "area")
    return [{"source": r[0], "upcoming": r[1], "missing": dict(zip(keys, r[2:], strict=True))} for r in rows]
