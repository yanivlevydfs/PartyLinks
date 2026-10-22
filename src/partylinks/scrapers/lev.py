"""Lev cinemas (lev.co.il, "קולנוע לב") - the art-house chain's own box office.

The "now showing" sidebar of every page is filled by the theme's ``ajax_data.php``
(``action=movie_on_location_new&loc=<branch name>&date=YYYY-MM-DD``): an HTML list with one
``<li>`` per screening (title, ``HH:MM``, order link with the screening ``pcode``, movie page
link). One request per branch and day (7 branches x ``days_ahead``). The movie page
(``/movies/<slug>/``) adds the poster, synopsis, country/year/language/runtime line, cast
and age rating: one request per distinct movie (~35, ~0.5 MB each - the page also dumps
debug arrays). A failing movie page only drops those extras. The branch list carries no
addresses, so they live in settings. Prices are a flat table on /prices/, not per screening:
none are given. robots.txt allows everything but ``/wp-admin`` (admin-ajax allowed).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from typing import Any, NamedTuple
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict
from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, to_int
from partylinks.scrapers.base import BaseScraper, SourceSettings

_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
_AGE = re.compile(r"(\d{1,2})\s*\+")
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
# A screening listed before this hour runs after midnight of the listed day.
_NIGHT_HOUR = 5


class LevBranch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The branch as the site's ``loc`` parameter names it ("לב תל אביב").
    name: str
    venue_name: str
    address: str | None = None
    city: str | None = None


class LevSettings(SourceSettings):
    site_url: str
    showtimes_path: str
    showtimes_action: str = "movie_on_location_new"
    # Days listed, today included.
    days_ahead: int = 3
    branches: dict[str, LevBranch]


class Screening(NamedTuple):
    title: str
    clock: str  # HH:MM as listed
    movie_url: str
    order_url: str | None
    pcode: str | None


class LevScraper(BaseScraper[LevSettings]):
    name = "lev"
    settings_model = LevSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(ZoneInfo(self.timezone)).date()
        slots = [(branch_id, today + timedelta(days=i)) for branch_id in s.branches for i in range(s.days_ahead)]

        async def listing(slot: tuple[str, date]) -> tuple[str, date, list[Screening]]:
            branch_id, day = slot
            params = {
                "clang": "he",
                "action": s.showtimes_action,
                "loc": s.branches[branch_id].name,
                "date": day.isoformat(),
            }
            return branch_id, day, parse_showtimes(await self.get_text(s.site_url + s.showtimes_path, params=params))

        listings = [x async for x in self.stream_isolated(slots, listing, lambda slot: f"listing {slot[0]} {slot[1]}")]
        movie_urls = sorted({sc.movie_url for _, _, screenings in listings for sc in screenings})
        self.log.info("listed %d screenings of %d movies", sum(len(x[2]) for x in listings), len(movie_urls))

        async def detail(url: str) -> tuple[str, dict[str, Any]]:
            return url, parse_movie(await self.get_text(url))

        movies = dict([x async for x in self.stream_isolated(movie_urls, detail, lambda url: f"movie {unquote(url)}")])
        for branch_id, day, screenings in listings:
            for event in build_events(screenings, branch_id, s.branches[branch_id], day, movies, self.timezone):
                yield event


def parse_showtimes(page_html: str) -> list[Screening]:
    """The sidebar list of one branch and day (commented-out duplicates are ignored by the parser)."""
    screenings = []
    for li in LexborHTMLParser(page_html).css("li"):
        order, link = li.css_first("a.topmenua"), li.css_first("a.smovielink")
        span = order.css_first("span") if order else None
        movie_url = clean_str(link.attributes.get("href")) if link else None
        if not order or not span or not movie_url or not _TIME.match(span.text(strip=True)):
            continue
        clock = span.text(strip=True)
        span.decompose()
        title = clean_str(order.text(strip=True))
        if title:
            href, pcode = order.attributes.get("href"), order.attributes.get("data-pcode")
            screenings.append(Screening(title, clock, movie_url, clean_str(href), clean_str(pcode)))
    return screenings


def parse_movie(page_html: str) -> dict[str, Any]:
    """Poster, synopsis and the facts line of a movie page (missing parts are left out)."""
    tree = LexborHTMLParser(page_html)

    def text(selector: str) -> str | None:
        node = tree.css_first(selector)
        return clean_str(node.text(separator=" ").replace("\xa0", " ")) if node else None

    movie: dict[str, Any] = {"title": text(".movie_title h1"), "english_title": text(".movie_title h2")}
    poster = tree.css_first(".movie_image noscript img") or tree.css_first(".movie_image img")
    if poster:
        movie["poster"] = clean_str(poster.attributes.get("src"))
        if movie["poster"] and movie["poster"].startswith("data:"):  # lazy-load placeholder
            movie["poster"] = clean_str(poster.attributes.get("data-src"))
    if content := tree.css_first(".movie_content"):
        if heading := content.css_first("h2"):  # "תקציר"
            heading.decompose()
        movie["synopsis"] = html_to_text(content.html)
    # "ישראל ,2026 | עברית | 117 דקות"
    for part in (p.strip(" ,") for p in (text(".allwithlinemobile") or "").split("|")):
        if "דקות" in part:
            movie["runtime_minutes"] = to_int(part.replace("דקות", "").strip())
        elif year := _YEAR.search(part):
            movie["year"] = to_int(year.group())
            movie["country"] = clean_str(part.replace(year.group(), "").strip(" ,"))
        elif part:
            movie["language"] = part
    casts = tree.css_first(".movie_casts")
    for line in (html_to_text(casts.html) or "").splitlines() if casts else []:
        key, _, value = line.partition(":")
        if key.strip() == "בימוי":
            movie["directors"] = value.strip()
        elif key.strip() == "משחק":
            movie["cast"] = value.strip()
    for age in tree.css(".movie_age"):
        label = age.text(strip=True)
        if label.startswith("הגבלה"):
            movie["rating"] = clean_str(label.partition(":")[2])
    code = tree.css_first("#get_locations_single option[value]")
    movie["movie_code"] = clean_str(code.attributes.get("value")) if code else None
    return {k: v for k, v in movie.items() if v not in (None, "")}


def movie_id(movie_url: str) -> str:
    """The movie page slug, decoded ("האהבות-שלנו"): the only id the listing gives."""
    return unquote(urlsplit(movie_url).path.rstrip("/").rpartition("/")[2])


def build_events(
    screenings: list[Screening],
    branch_id: str,
    branch: LevBranch,
    day: date,
    movies: dict[str, dict[str, Any]],
    timezone: str,
) -> list[ScrapedEvent]:
    """One event per movie of this branch and day, starting at its first screening."""
    tz = ZoneInfo(timezone)
    by_movie: dict[str, list[tuple[datetime, Screening]]] = {}
    for sc in screenings:
        hour, minute = (int(x) for x in sc.clock.split(":"))
        start = datetime.combine(day, time(hour, minute), tz)
        if hour < _NIGHT_HOUR:
            start += timedelta(days=1)
        by_movie.setdefault(sc.movie_url, []).append((start, sc))
    events = []
    for url, shows in by_movie.items():
        shows.sort(key=lambda x: x[0])
        movie, mid = movies.get(url, {}), movie_id(url)
        rating = movie.get("rating")
        age = _AGE.search(rating) if rating else None
        times = "הקרנות: " + ", ".join(sc.clock for _, sc in shows)
        facts = (
            "movie_code",
            "english_title",
            "runtime_minutes",
            "rating",
            "language",
            "country",
            "year",
            "directors",
            "cast",
        )
        events.append(
            ScrapedEvent(
                source=LevScraper.name,
                source_event_id=f"{mid}:{branch_id}:{day.isoformat()}",
                url=url,
                title=movie.get("title") or shows[0][1].title,
                description="\n\n".join(x for x in (movie.get("synopsis"), times) if x),
                start_at=shows[0][0],
                venue_name=branch.venue_name,
                address=branch.address,
                city=branch.city,
                source_genres=["קולנוע"],
                min_age=int(age.group(1)) if age else None,
                image_url=movie.get("poster"),
                extra={
                    "showtimes": [start.isoformat() for start, _ in shows],
                    "movie_id": mid,
                    "branch_id": branch_id,
                    "purchase_url": shows[0][1].order_url,
                    "screening_codes": [sc.pcode for _, sc in shows if sc.pcode],
                    **{k: movie[k] for k in facts if k in movie},
                },
            )
        )
    return events
