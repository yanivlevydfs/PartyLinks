"""GET /facets/venues and GET /facets/artists: one page of the index, searched and filtered.

Both read the same cached JSON as /categories?anywhere=true and /artists/tags (no new database query); each
answer is cached by its URL like the rest of the API."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from partylinks.db import EventRow, ImageRow, upcoming_filter
from partylinks.parsing import is_site_logo

router = APIRouter(tags=["facets"])

ABROAD = re.compile(r"^[A-Z]{2}$")  # ISO country codes; Israel's region keys are lower case
_STRIP = re.compile(r"[\s\-_.·'\"׳״`’]")
_LEAD = re.compile(r"^[\W_]+")  # "📍 Rio", "'t Hart": filed under their first letter


def norm(s: str) -> str:
    """Case-insensitive, no spaces or punctuation: "gray club" finds "Gray Club תל אביב-יפו"."""
    return _STRIP.sub("", s.casefold())


def az_key(name: str) -> tuple[bool, str]:
    """A to Z by the first letter, names starting with a digit or a sign after the letters ("#" in a phone book)."""
    key = _LEAD.sub("", name).casefold()
    return (not key[:1].isalpha(), key)


def named(value: str) -> bool:
    """ "-", ".", "00:00", "85/15": scraped venue fields with no name in them."""
    return any(c.isalpha() for c in value)


def search(items: list[dict[str, Any]], q: str) -> tuple[list[dict[str, Any]], bool]:
    """Matches of ``q``, best first: the whole name, then names starting with it, then a word starting with it,
    then anywhere in the name; more events first within each. A typo at the end ("יניבדד") finds nothing: drop
    letters from the end until names match (3 letters at least). Returns (matches, fuzzy)."""
    key = norm(q)
    if not key:
        return items, False

    def matches(k: str) -> list[dict[str, Any]]:
        def rank(f: dict[str, Any]) -> int | None:
            name = norm(f["value"])
            if k not in name:
                return None
            if name == k:
                return 0
            if name.startswith(k):
                return 1
            words = re.split(r"[\s\-_./·]+", f["value"].casefold())
            return 2 if any(norm(w).startswith(k) for w in words if w) else 3

        ranked = [(r, f) for f in items if (r := rank(f)) is not None]
        ranked.sort(key=lambda rf: (rf[0], -rf[1]["events"], az_key(rf[1]["value"])))
        return [f for _, f in ranked]

    found = matches(key)
    if found:
        return found, False
    while len(key) > 3 and not found:
        key = key[:-1]
        found = matches(key)
    return found, bool(found)


def counted(lists: Iterable[list[str]], keep: Callable[[str], bool] = lambda v: True) -> list[tuple[str, int]]:
    """Each value with how many artists carry it, most common first."""
    n: Counter[str] = Counter()
    for values in lists:
        n.update(v for v in set(values) if keep(v))
    return n.most_common()


def artist_filter(
    facets: list[dict[str, Any]],
    tags: dict[str, dict[str, list[str]]],
    groups: list[dict[str, Any]],
    cat: str | None,
    genre: str | None,
    where: str | None,
    place: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The artists the filter keeps, and its chips' counts. Each row counts the artists the other rows keep,
    so a chip never leads to an empty list (the same rules the page ran before, artist_filter/ArtistFilter.tsx)."""
    members = next((g.get("members") or [] for g in groups if g["value"] == cat), [])

    def of(a: dict[str, Any]) -> dict[str, list[str]] | None:
        return tags.get(a["value"].casefold())

    def in_where(w: list[str]) -> bool:
        if not where:
            return True
        return any(ABROAD.match(x) for x in w) if where == "abroad" else any(not ABROAD.match(x) for x in w)

    def keep(a: dict[str, Any], skip: str | None) -> bool:
        tg = of(a)
        if not tg:
            return not (cat or genre or where or place)
        return (
            (skip == "cat" or not cat or cat in tg["g"])
            and (skip == "genre" or not genre or genre in tg["g"])
            and (skip == "where" or in_where(tg["w"]))
            and (skip == "place" or not place or place in tg["w"])
        )

    def pool(skip: str, part: str) -> list[list[str]]:
        return [(of(a) or {}).get(part, []) for a in facets if keep(a, skip)]

    wheres = counted(pool("where", "w"))
    chips = {
        "cats": counted(pool("cat", "g")),
        "genres": counted(pool("genre", "g"), lambda g: g in members) if cat else [],
        "here": sum(1 for w, _ in wheres if not ABROAD.match(w)),
        "away": sum(1 for w, _ in wheres if ABROAD.match(w)),
        "places": counted(pool("place", "w"), lambda w: (where == "abroad") == bool(ABROAD.match(w))) if where else [],
        "groups": [{"value": g["value"], "events": g["events"]} for g in groups],
    }
    return [a for a in facets if keep(a, None)], chips


def _parsed(state: Any, key: tuple[str, str], build: Callable[[], bytes]) -> Any:
    """The cached JSON of another endpoint, parsed once per cached body."""
    raw = state.response_cache.get(key, build)[0]
    memo = getattr(state, "facet_list_parsed", {})
    if key not in memo or memo[key][0] is not raw:
        memo[key] = (raw, json.loads(raw))
        state.facet_list_parsed = memo
    return memo[key][1]


def pictures_body(state: Any) -> bytes:
    """A picture for each artist and venue from its upcoming events (the soonest first): the index shows it when
    the artist or venue has no photo or logo of its own. Stored pictures only, never a drawn card."""
    with state.db.session(write=False) as session:
        rows = session.execute(
            select(EventRow.lineup, EventRow.venue_name, ImageRow.id, ImageRow.url)
            .join(ImageRow.event)
            .where(upcoming_filter(), EventRow.is_primary.is_(True), ImageRow.downloaded_at.is_not(None))
            .order_by(EventRow.start_at, ImageRow.position, ImageRow.id)
        ).all()
    artists: dict[str, str] = {}
    venues: dict[str, str] = {}
    for lineup, venue, image_id, url in rows:
        if is_site_logo(url):
            continue
        path = f"/images/{image_id}/640.webp"
        if venue:
            venues.setdefault(venue.casefold(), path)
        for name in lineup or []:
            if name:
                artists.setdefault(str(name).casefold(), path)
    return json.dumps({"a": artists, "v": venues}, separators=(",", ":")).encode()


def _page(state: Any, items: list[dict[str, Any]], page: int, size: int, kind: str) -> list[dict[str, Any]]:
    """The page's rows, each with a picture: its own, else one of its events'."""
    pictures = _parsed(state, ("/facets/pictures", ""), lambda: pictures_body(state))[kind]
    rows = items[(page - 1) * size : page * size]
    return [r if r.get("image_url") else {**r, "image_url": pictures.get(r["value"].casefold())} for r in rows]


PageQ = Annotated[int, Query(ge=1, le=1000)]
SizeQ = Annotated[int | None, Query(ge=1, le=1000)]
TextQ = Annotated[str | None, Query(max_length=100)]


@router.get("/facets/venues")
def venues(request: Request, q: TextQ = None, area: TextQ = None, page: PageQ = 1, page_size: SizeQ = None) -> Response:
    state: Any = request.app.state
    size = page_size or state.settings.site.web.index_page_size

    def body() -> bytes:
        cats = _parsed(state, ("/categories", "anywhere=true"), state.anywhere_categories)
        all_venues = [f for f in cats["venues"] if named(f["value"])]
        areas = list(dict.fromkeys(f["area"] for f in all_venues if f.get("area")))
        shown = [f for f in all_venues if not area or f.get("area") == area]
        if q:
            shown, fuzzy = search(shown, q)
        else:
            shown, fuzzy = sorted(shown, key=lambda f: (-f["events"], az_key(f["value"]))), False  # busiest first
        out = {
            "items": _page(state, shown, page, size, "v"),
            "total": len(shown),
            "page": page,
            "fuzzy": fuzzy,
            "areas": areas,
        }
        return json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()

    return Response(
        state.response_cache.get((request.url.path, request.url.query), body)[0], media_type="application/json"
    )


@router.get("/facets/artists")
def artists(
    request: Request,
    q: TextQ = None,
    cat: TextQ = None,
    genre: TextQ = None,
    where: TextQ = None,
    place: TextQ = None,
    sort: TextQ = None,
    page: PageQ = 1,
    page_size: SizeQ = None,
) -> Response:
    from partylinks.artist_filter.routes import tags_body

    state: Any = request.app.state
    size = page_size or state.settings.site.web.index_page_size

    def body() -> bytes:
        cats = _parsed(state, ("/categories", "anywhere=true"), state.anywhere_categories)
        tags = _parsed(state, ("/artists/tags", ""), lambda: tags_body(state))
        shown, chips = artist_filter(cats["lineup"], tags, cats.get("genre_groups") or [], cat, genre, where, place)
        if q:
            shown, fuzzy = search(shown, q)
        elif sort == "popular":
            shown, fuzzy = sorted(shown, key=lambda f: -f["events"]), False
        else:
            shown, fuzzy = sorted(shown, key=lambda f: az_key(f["value"])), False
        out = {
            "items": _page(state, shown, page, size, "a"), "total": len(shown), "page": page, "fuzzy": fuzzy,
            "all": len(cats["lineup"]), "chips": chips,
        }  # fmt: skip
        return json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()

    return Response(
        state.response_cache.get((request.url.path, request.url.query), body)[0], media_type="application/json"
    )
