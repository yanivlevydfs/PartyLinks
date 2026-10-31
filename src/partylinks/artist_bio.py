"""A short "about" text for an artist's page, written from the site's own listings, in Hebrew and English.

No text is copied from a ticket site (their bios are theirs, and the same words on two sites rank only
the original): how many upcoming shows, the genres they are tagged with, the next one, the venues and
cities, and the countries abroad. Shown by the server-rendered /artist page and, through /landing-info,
by the web app's artist page.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from partylinks.config import Settings
from partylinks.countries import country_name
from partylinks.db import EventRow, lineup_has, upcoming_filter

TOP = 3  # venues / cities / genres / countries named


@dataclass
class Facts:
    shows: int
    next_date: date | None = None
    next_venue: str | None = None
    next_city: str | None = None
    genres: list[str] = field(default_factory=list)  # labels, in the page's language
    venues: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)  # abroad


def facts(session: Session, cfg: Settings, lang: str, name: str) -> Facts | None:
    """What the upcoming listings say about ``name``; None when the artist has no upcoming show."""
    from partylinks.seo import genre_label  # local: seo imports this module

    cols = (EventRow.local_date, EventRow.venue_name, EventRow.city, EventRow.country, EventRow.genres)
    rows = session.execute(
        select(*cols).where(upcoming_filter(), EventRow.is_primary.is_(True), lineup_has(name))
        .order_by(EventRow.start_at.asc().nulls_last())
    ).all()
    if not rows:
        return None
    en_city = {c.he: c.en for c in cfg.enrichment.cities}
    city = (lambda c: en_city.get(c, c)) if lang == "en" else (lambda c: c)
    first = rows[0]

    def top(values: list[str | None]) -> list[str]:
        return [v for v, _ in Counter(v for v in values if v).most_common(TOP)]

    return Facts(
        shows=len(rows),
        next_date=first.local_date,
        next_venue=first.venue_name,
        next_city=city(first.city) if first.city else None,
        genres=[genre_label(g, lang) for g in top([g for r in rows for g in r.genres or []])],
        venues=top([r.venue_name for r in rows]),
        cities=[city(c) for c in top([r.city for r in rows if not r.country])],
        countries=[country_name(c, lang) for c in top([r.country for r in rows])],
    )


def _join(items: list[str], lang: str) -> str:
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + (f" ו־{items[-1]}" if lang == "he" else f" and {items[-1]}")


def compose(lang: str, name: str, f: Facts, site: str) -> str:
    from partylinks.seo import DAYS

    he = lang == "he"
    when = f"{DAYS[lang][f.next_date.weekday()]} {f.next_date:%d/%m/%Y}" if f.next_date else ""
    where = ", ".join(p for p in (f.next_venue, f.next_city) if p)
    genres = _join(f.genres, lang)
    if he:
        shows = "הופעה קרובה אחת" if f.shows == 1 else f"{f.shows} הופעות קרובות"
        parts = [f"ל־{name} יש {shows} ב־{site}" + (f", בעיקר {genres}." if genres else ".")]
        if when:
            parts.append(f"הקרובה: {when}" + (f" ב{where}." if where else "."))
        if len(f.venues) > 1:
            parts.append(f"מקומות: {_join(f.venues, lang)}.")
        if len(f.cities) > 1:
            parts.append(f"ערים: {_join(f.cities, lang)}.")
        if f.countries:
            parts.append(f"בחו״ל: {_join(f.countries, lang)}.")
    else:
        shows = "1 upcoming show" if f.shows == 1 else f"{f.shows} upcoming shows"
        parts = [f"{name} has {shows} on {site}" + (f", mostly {genres}." if genres else ".")]
        if when:
            parts.append(f"Next: {when}" + (f" at {where}." if where else "."))
        if len(f.venues) > 1:
            parts.append(f"Venues: {_join(f.venues, lang)}.")
        if len(f.cities) > 1:
            parts.append(f"Cities: {_join(f.cities, lang)}.")
        if f.countries:
            parts.append(f"Abroad: {_join(f.countries, lang)}.")
    return " ".join(parts)


def bio(session: Session, cfg: Settings, lang: str, name: str) -> str | None:
    f = facts(session, cfg, lang, name)
    return compose(lang, name, f, cfg.site.name) if f else None

