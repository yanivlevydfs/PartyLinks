"""The worker's emails: welcome (after sign-up), instant alerts (after a matcher pass, at most hourly per
user) and the weekly digest (Thursday morning). Each kind obeys its admin switch, each user's own choices,
and records a run. Nothing is sent without the Resend key."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from partylinks.accounts.models import UserRow
from partylinks.alerts.models import AlertHitRow, AlertRow
from partylinks.config import Settings
from partylinks.db import Database, EventRow, upcoming_filter, utcnow
from partylinks.mail import send as mailer
from partylinks.mail import tokens
from partylinks.mail.models import EmailRunRow, MailSwitchesRow
from partylinks.mail.render import TEXT, Section, link, render
from partylinks.saved.models import SavedEventRow
from partylinks.saved.routes import cards

log = logging.getLogger(__name__)
INSTANT_MAX = 10
DIGEST_DAYS = 10  # matches for events in the next 10 days
SAVED_DAYS = 7
FILLER = 6


def switches(session: Session) -> MailSwitchesRow:
    row = session.get(MailSwitchesRow, 1)
    if row is None:
        row = MailSwitchesRow(id=1, welcome=True, instant=True, digest=True)
        session.add(row)
        session.flush()
    return row


def _on(settings: Settings, db: Database, kind: str) -> bool:
    if not (settings.accounts.enabled and mailer.ready(settings)):
        return False
    with db.session() as session:
        return bool(getattr(switches(session), kind))


def unsubscribe_url(settings: Settings, user_id: str, kind: str) -> str:
    return f"{settings.site.base_url.rstrip('/')}/api/email/unsubscribe?t={tokens.make(settings, user_id, kind)}"


def _message(settings: Settings, user: UserRow, kind: str, stop: str, **parts: Any) -> mailer.Message:
    url = unsubscribe_url(settings, user.id, stop)
    subject, html, text = render(settings, kind=kind, lang=user.locale, name=user.name, unsubscribe_url=url, **parts)
    reply = settings.site.contact_email
    return mailer.Message(user.id, user.email, subject, html, text, url, reply_to=reply, tags={"kind": kind})


def _record(
    db: Database, kind: str, started: datetime, sent: int, failed: int, skipped: int, error: str | None
) -> None:
    with db.session() as session:
        session.add(EmailRunRow(kind=kind, started_at=started, finished_at=utcnow(), sent=sent, failed=failed,
                                skipped=skipped, error=error))  # fmt: skip
    log.info("mail %s: %d sent, %d failed, %d skipped", kind, sent, failed, skipped)


def _send(settings: Settings, db: Database, kind: str, key: str, messages: list[mailer.Message], skipped: int,
          started: datetime) -> list[str]:  # fmt: skip
    """Sends and records the run; returns the ids of the users whose email went."""
    try:
        sent, failed, error = mailer.send(settings, messages, key) if messages else ([], [], None)
    except mailer.MailAccountError as exc:
        _record(db, kind, started, 0, len(messages), skipped, str(exc))
        return []
    _record(db, kind, started, len(sent), len(failed), skipped, error)
    return [m.user_id for m in sent]


# --------------------------------------------------------------------------- welcome
def welcome_parts(settings: Settings, user: UserRow) -> dict[str, Any]:
    t = TEXT[user.locale if user.locale in TEXT else "he"]
    return {
        "title": t["welcome_title"].format(name=user.name.split(" ")[0]), "subject": t["welcome_subject"],
        "intro": t["welcome_body"], "steps": t["welcome_steps"], "sections": [], "why": t["welcome_why"],
        "button": (t["welcome_button"], link(settings, "/", "welcome", user.locale)),
    }  # fmt: skip


def welcome(settings: Settings, db: Database) -> None:
    if not _on(settings, db, "welcome"):
        return
    started = utcnow()
    with db.session() as session:
        users = session.scalars(
            select(UserRow).where(
                UserRow.welcome_sent_at.is_(None), UserRow.blocked_at.is_(None), UserRow.created_at < started - timedelta(minutes=2)
            ).limit(500)
        ).all()  # fmt: skip
        session.expunge_all()
    if not users:
        return
    messages = [_message(settings, u, "welcome", "all", **welcome_parts(settings, u)) for u in users]
    done = _send(settings, db, "welcome", f"welcome:{started:%Y%m%d%H%M}", messages, 0, started)
    with db.session() as session:
        session.execute(update(UserRow).where(UserRow.id.in_(done)).values(welcome_sent_at=utcnow()))


# --------------------------------------------------------------------------- instant
def instant(settings: Settings, db: Database) -> None:
    """New matches of alerts set to "email me at once": one email per user, at most hourly."""
    if not _on(settings, db, "instant"):
        return
    started = utcnow()
    wait = started - timedelta(minutes=settings.mail.instant_min_interval_minutes)
    with db.session() as session:
        pending = session.execute(
            select(AlertHitRow.user_id, AlertHitRow.event_id)
            .join(AlertRow, AlertRow.id == AlertHitRow.alert_id)
            .join(UserRow, UserRow.id == AlertHitRow.user_id)
            .where(
                AlertHitRow.instant_sent_at.is_(None), AlertRow.instant.is_(True), AlertRow.active.is_(True),
                UserRow.blocked_at.is_(None), (UserRow.last_instant_at.is_(None)) | (UserRow.last_instant_at < wait),
            )
            .order_by(AlertHitRow.matched_at)
        ).all()  # fmt: skip
        by_user: dict[str, list[str]] = {}
        for user_id, event_id in pending:
            by_user.setdefault(user_id, []).append(event_id)
        users = {u.id: u for u in session.scalars(select(UserRow).where(UserRow.id.in_(by_user)))}
        messages, skipped = [], 0
        for user_id, ids in by_user.items():
            events = cards(session, list(dict.fromkeys(ids)))[:INSTANT_MAX]
            if not events:
                skipped += 1
                continue
            user = users[user_id]
            t = TEXT[user.locale if user.locale in TEXT else "he"]
            subject = (
                t["instant_subject_1"].format(title=events[0]["title"])
                if len(events) == 1
                else t["instant_subject"].format(n=len(events))
            )
            messages.append(_message(
                settings, user, "instant", "instant", title=t["instant_title"], subject=subject,
                sections=[Section("", events)], button=(t["alerts_button"], link(settings, "/alerts", "instant", user.locale)),
            ))  # fmt: skip
        session.expunge_all()
    done = _send(settings, db, "instant", f"instant:{started.timestamp():.0f}", messages, skipped, started)
    with db.session() as session:
        now = utcnow()
        # Every pending match of a user it went to (more than INSTANT_MAX are dropped: they are in the bell).
        session.execute(
            update(AlertHitRow).where(AlertHitRow.user_id.in_(done), AlertHitRow.instant_sent_at.is_(None)).values(instant_sent_at=now)
        )  # fmt: skip
        session.execute(update(UserRow).where(UserRow.id.in_(done)).values(last_instant_at=now))


# --------------------------------------------------------------------------- digest
def digest_parts(settings: Settings, session: Session, user: UserRow) -> tuple[dict[str, Any], list[str]]:
    """The weekly email of one user, and the matches it shows (stamped once it goes)."""
    from partylinks.api import build_event_filters  # local: api imports the account routes

    lang = user.locale if user.locale in TEXT else "he"
    t = TEXT[lang]
    tz = ZoneInfo(settings.timezone)
    today = datetime.now(tz).date()
    soon = datetime.combine(today + timedelta(days=SAVED_DAYS), datetime.max.time(), tzinfo=tz)
    later = datetime.combine(today + timedelta(days=DIGEST_DAYS), datetime.max.time(), tzinfo=tz)

    saved_ids = list(session.scalars(select(SavedEventRow.event_id).where(SavedEventRow.user_id == user.id)))
    saved = [c for c in cards(session, saved_ids) if c["start_at"] and datetime.fromisoformat(c["start_at"]) <= soon]
    hit_ids = list(session.scalars(
        select(AlertHitRow.event_id)
        .join(EventRow, EventRow.id == AlertHitRow.event_id)
        .where(AlertHitRow.user_id == user.id, AlertHitRow.digest_sent_at.is_(None), upcoming_filter(), EventRow.start_at <= later)
        .order_by(EventRow.start_at)
    ))  # fmt: skip
    shown = {c["id"] for c in saved}
    matches = [c for c in cards(session, list(dict.fromkeys(hit_ids))) if c["id"] not in shown][
        : settings.mail.digest_max_events
    ]
    shown |= {c["id"] for c in matches}
    sections = [Section(t["digest_saved"], saved), Section(t["digest_matches"], matches)]
    if len(shown) < 4:  # a quiet week: this weekend's free events, then the weekend's highlights
        weekend = {"weekend": True, "date_to": datetime.combine(today + timedelta(days=7), datetime.max.time())}
        for title, extra in ((t["digest_free"], {"free_entry": True}), (t["digest_weekend"], {})):
            ids = session.scalars(
                select(EventRow.id).where(*build_event_filters(settings, **weekend, **extra)).order_by(EventRow.group_size.desc(), EventRow.start_at).limit(FILLER * 2)
            )  # fmt: skip
            more = [c for c in cards(session, list(ids)) if c["id"] not in shown][:FILLER]
            shown |= {c["id"] for c in more}
            sections.append(Section(title, more))
    count = sum(len(s.events) for s in sections)
    parts = {
        "title": t["digest_title"], "subject": t["digest_subject"].format(n=count), "sections": sections,
        "button": (t["alerts_button"], link(settings, "/alerts", "digest", lang)),
    }  # fmt: skip
    return parts, hit_ids


def digest(settings: Settings, db: Database) -> None:
    """Thursday morning: everyone who asked for it, once a week."""
    if not _on(settings, db, "digest"):
        return
    started = utcnow()
    week = datetime.now(ZoneInfo(settings.timezone)).strftime("%G-W%V")
    messages, skipped, shown_hits = [], 0, {}
    with db.session() as session:
        users = session.scalars(
            select(UserRow).where(
                UserRow.digest_opt_in.is_(True), UserRow.blocked_at.is_(None),
                (UserRow.last_digest_at.is_(None)) | (UserRow.last_digest_at < started - timedelta(days=6)),
            )
        ).all()  # fmt: skip
        for user in users:
            parts, hit_ids = digest_parts(settings, session, user)
            if not any(s.events for s in parts["sections"]):
                skipped += 1
                continue
            shown_hits[user.id] = hit_ids
            messages.append(_message(settings, user, "digest", "digest", **parts))
        session.expunge_all()
    done = _send(settings, db, "digest", f"digest:{week}", messages, skipped, started)
    with db.session() as session:
        now = utcnow()
        session.execute(update(UserRow).where(UserRow.id.in_(done)).values(last_digest_at=now))
        for user_id in done:
            session.execute(
                update(AlertHitRow)
                .where(AlertHitRow.user_id == user_id, AlertHitRow.event_id.in_(shown_hits[user_id]))
                .values(digest_sent_at=now)
            )
