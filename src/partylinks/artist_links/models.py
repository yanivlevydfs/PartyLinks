"""The ``artist_links`` table (the lookup cache: hits and misses), name keys, and the links the site shows.

On the app's one Base, so ``Database.create_schema`` creates it (partylinks.db imports this module).
"""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import Boolean, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from partylinks.artist_links.config import ArtistLinksSettings
from partylinks.db import Base, NullableJSON, UTCDateTime, utcnow

Link = dict[str, str]  # {"service": "spotify", "url": "https://open.spotify.com/artist/..."}


class ArtistLinkRow(Base):
    """One MusicBrainz answer per lineup name: a hit (``found``, its MBID and links) or a miss."""

    __tablename__ = "artist_links"

    key: Mapped[str] = mapped_column(Text, primary_key=True)  # name_key(name)
    name: Mapped[str] = mapped_column(Text)  # the lineup spelling looked up
    mbid: Mapped[str | None] = mapped_column(Text)
    links: Mapped[list[Link] | None] = mapped_column(NullableJSON)
    found: Mapped[bool] = mapped_column(Boolean, default=False)
    looked_up_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


_PUNCT = re.compile(r"[\W_]+")


def name_key(name: str) -> str:
    """'Dalí', 'DALI', 'dali!' -> 'dali'; Hebrew niqqud and accents dropped; punctuation is a space."""
    text = unicodedata.normalize("NFKD", name.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return _PUNCT.sub(" ", text).strip()


# Host (without www. / m. / open.) -> service. Subdomains count: "artist.bandcamp.com".
_HOSTS = {
    "spotify.com": "spotify", "youtube.com": "youtube", "youtu.be": "youtube", "music.youtube.com": "youtube",
    "music.apple.com": "apple_music", "itunes.apple.com": "apple_music", "soundcloud.com": "soundcloud",
    "deezer.com": "deezer", "bandcamp.com": "bandcamp", "instagram.com": "instagram", "tiktok.com": "tiktok",
    "facebook.com": "facebook", "twitter.com": "x", "x.com": "x",
}  # fmt: skip
# Hosts that are databases about the artist, not the artist's own page: never shown as "website".
_NOT_WEBSITE = ("wikidata.org", "wikipedia.org", "discogs.com", "allmusic.com", "last.fm", "musicbrainz.org",
                "songkick.com", "setlist.fm", "residentadvisor.net", "ra.co", "beatport.com", "imdb.com", "genius.com")  # fmt: skip


def service_of(url: str, homepage: bool = False) -> str | None:
    """'https://www.instagram.com/x' -> 'instagram'. ``homepage``: the artist's own site (MusicBrainz 'official homepage')."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = re.sub(r"^(www|m|open|he|en)\.", "", parts.hostname.lower())
    for known, service in _HOSTS.items():
        if host == known or host.endswith("." + known):
            return service
    if homepage and not any(host == h or host.endswith("." + h) for h in _NOT_WEBSITE):
        return "website"
    return None


def merge(urls: list[tuple[str, bool]], services: list[str]) -> list[Link]:
    """(url, is homepage) -> one link per service, first wins, in the configured order."""
    best: dict[str, str] = {}
    for url, homepage in urls:
        if (service := service_of(url, homepage)) in services:
            best.setdefault(service, url)
    return [{"service": s, "url": best[s]} for s in services if s in best]


# --------------------------------------------------------------------------- read side (web process)
_cache: tuple[float, dict[str, list[Link]]] = (0.0, {})
_cache_lock = threading.Lock()


def _load(session: Session, cfg: ArtistLinksSettings) -> dict[str, list[Link]]:
    """Every name with links: ticket-site profiles first (curated), then MusicBrainz, merged by service."""
    # Here, not at the top: db imports this module, and artistdb imports db (`python -m partylinks` loads artistdb first).
    from partylinks.artistdb import ArtistProfileRow

    urls: dict[str, list[tuple[str, bool]]] = {}
    for name, spotify, links in session.execute(
        select(ArtistProfileRow.name, ArtistProfileRow.spotify_url, ArtistProfileRow.links)
    ):
        urls.setdefault(name_key(name), []).extend((u, True) for u in [spotify, *(links or [])] if u)
    for key, links in session.execute(
        select(ArtistLinkRow.key, ArtistLinkRow.links).where(ArtistLinkRow.found.is_(True))
    ):
        urls.setdefault(key, []).extend((link["url"], link["service"] == "website") for link in links or [])
    return {k: merged for k, u in urls.items() if (merged := merge(u, cfg.services))}


def all_links(session: Session, cfg: ArtistLinksSettings) -> dict[str, list[Link]]:
    """name_key -> links, read from the database at most every ``cache_seconds``."""
    global _cache
    loaded_at, data = _cache
    if time.monotonic() - loaded_at < cfg.cache_seconds and loaded_at:
        return data
    with _cache_lock:
        if time.monotonic() - _cache[0] < cfg.cache_seconds and _cache[0]:
            return _cache[1]
        data = _load(session, cfg)
        _cache = (time.monotonic(), data)
        return data


def clear_cache() -> None:
    global _cache
    _cache = (0.0, {})


def links_for(session: Session, cfg: ArtistLinksSettings, names: list[str]) -> dict[str, list[Link]]:
    """Lineup name -> its links, for names that have any."""
    known = all_links(session, cfg)
    return {n: known[k] for n in names if (k := name_key(n)) in known}
