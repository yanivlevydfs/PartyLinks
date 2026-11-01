"""📅 under a card: the event as an .ics file (Apple, and any calendar app) and a Google Calendar link,
the same entry as the site's "add to calendar" (web/src/ui.tsx AddToCalendar)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

from icalendar import Calendar, Event

from partylinks.db import EventRow, utcnow

NO_END = timedelta(hours=3)  # no end time listed: a guess the user can edit in their calendar (as the site)


def _span(row: EventRow) -> tuple[date, date] | tuple[datetime, datetime]:
    """(start, end): datetimes, or dates for an all-day entry (a date alone, no time from the seller)."""
    if not row.time_known and row.local_date:
        return row.local_date, row.local_date + timedelta(days=1)
    start = row.start_at or utcnow()
    return start, row.end_at if row.end_at and row.end_at > start else start + NO_END


def _where(row: EventRow) -> str:
    return ", ".join(p for p in (row.venue_name, row.address, row.city) if p)


def ics(row: EventRow, url: str, site: str) -> bytes:
    start, end = _span(row)
    entry = Event()
    entry.add("uid", f"{row.id}@{site}")
    entry.add("dtstamp", utcnow())
    entry.add("dtstart", start)
    entry.add("dtend", end)
    entry.add("summary", row.title)
    if where := _where(row):
        entry.add("location", where)
    entry.add("url", url)
    entry.add("description", url)
    cal = Calendar()
    cal.add("prodid", f"-//{site}//bot//EN")
    cal.add("version", "2.0")
    cal.add_component(entry)
    return cal.to_ical()


def google(row: EventRow, url: str) -> str:
    start, end = _span(row)
    if isinstance(start, datetime) and isinstance(end, datetime):
        dates = f"{start.astimezone(UTC):%Y%m%dT%H%M%SZ}/{end.astimezone(UTC):%Y%m%dT%H%M%SZ}"
    else:
        dates = f"{start:%Y%m%d}/{end:%Y%m%d}"
    query = {"action": "TEMPLATE", "text": row.title, "location": _where(row), "details": url, "dates": dates}
    return "https://calendar.google.com/calendar/render?" + urlencode(query)
