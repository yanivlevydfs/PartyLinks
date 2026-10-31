"""Venue profiles a ticket site curates: logo, "about" text, links, location (table ``venue_profiles``).

Eventer keeps a catalog of ~80 clubs and halls (``/eventContainer/Venue/listByType.js``) and a page per
venue (``/Venue/<linkName>/byLink.js``). The Eventer scraper reads them each run (``eventer.py`` here);
the service saves them. Logos are converted to one WebP (``LOGO_WIDTH``) and kept in the row, served at
``/venue-logos/<key>.webp``.

A profile's names ("מון צ׳יילד | Moon Child") rarely equal our venue name ("Moonchild - מון צ'ילד"), so an
event the site sells names its venue's profile (``extra[VENUE_PROFILE]``), and the venue name that event
ends up under (after venues.py merges spellings) is the profile's. Venues with no such event match only on
the whole name (spaces, punctuation and words like "club" / "תל אביב" ignored), and not when far apart.
Looser rules (one name inside another, a shared word nearby) tied "קולנוע" to a cinema and Herzliya's hall
to Tel Aviv's on production data.
"""

from __future__ import annotations

import io
import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from PIL import Image, ImageOps
from sqlalchemy import Float, LargeBinary, Text, func, select
from sqlalchemy.orm import Mapped, Session, defer, mapped_column

from partylinks.db import Base, Database, NullableJSON, UTCDateTime, dialect_insert, utcnow, with_db_retry

VENUE_PROFILE = "venue_profile"  # events' extra key: the key of their venue's profile
LOGO_WIDTH = 960  # the venue page banner (~480px, 2x); cards show it smaller
FAR_M = 1500.0  # the same name farther than this is another place (a chain, a namesake in another city)
# Words that say nothing about which venue it is.
_GENERIC = {
    "club", "the", "tel", "aviv", "tlv", "telaviv", "yafo", "jaffa", "bar", "pub", "live", "music", "theatre", "theater",
    "park", "beach", "hall", "center", "centre", "night", "day", "and", "eilat", "haifa", "jerusalem",
    "מועדון", "תל", "אביב", "יפו", "בר", "פאב", "ה", "תיאטרון", "פארק", "אולם", "היכל", "בית", "מרכז", "קיבוץ", "חוף",
    "אילת", "חיפה", "ירושלים", "באר", "שבע", "ראשון", "לציון",
}


@dataclass
class VenueProfile:
    key: str  # "<source>-<site id>", ASCII: the logo's URL
    name: str  # as the site shows it, "מון צ׳יילד | Moon Child"
    source: str
    aliases: list[str] = field(default_factory=list)  # each spelling, "מון צ׳יילד", "Moon Child"
    about: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    links: list[str] = field(default_factory=list)
    profile_url: str | None = None
    logo_url: str | None = None
    logo: bytes | None = None  # the downloaded original, until saved as WebP


class VenueProfileRow(Base):
    __tablename__ = "venue_profiles"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str] | None] = mapped_column(NullableJSON)
    about: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    links: Mapped[list[str] | None] = mapped_column(NullableJSON)
    profile_url: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)  # the site's picture the WebP was made from
    logo: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)  # WebP
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


def to_webp(data: bytes, quality: int, width: int = LOGO_WIDTH) -> bytes | None:
    """The picture as WebP, at most ``width`` wide (never upscaled). None when it doesn't decode."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            picture = ImageOps.exif_transpose(im).convert("RGBA" if alpha else "RGB")
        picture.thumbnail((width, width * 4), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        picture.save(out, "WEBP", quality=quality, method=4)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - a broken picture: the profile is kept without a logo
        return None


def load_profiles(session: Session) -> list[VenueProfile]:
    """Every profile, without the logo bytes (``logo_url`` is None when no logo is stored)."""
    rows = session.scalars(select(VenueProfileRow).options(defer(VenueProfileRow.logo))).all()
    with_logo = set(session.scalars(select(VenueProfileRow.key).where(VenueProfileRow.logo.is_not(None))))
    return [
        VenueProfile(
            key=r.key, name=r.name, source=r.source, aliases=list(r.aliases or []), about=r.about, address=r.address,
            latitude=r.latitude, longitude=r.longitude, links=list(r.links or []), profile_url=r.profile_url,
            logo_url=r.logo_url if r.key in with_logo else None,
        )
        for r in rows
    ]  # fmt: skip


class VenueBook:
    def __init__(self, db: Database, quality: int) -> None:
        self.db = db
        self.quality = quality

    def profiles(self) -> list[VenueProfile]:
        with self.db.session(write=False) as session:
            return load_profiles(session)

    def logo(self, key: str) -> tuple[bytes, datetime] | None:
        with self.db.session(write=False) as session:
            row = session.execute(select(VenueProfileRow.logo, VenueProfileRow.updated_at).where(VenueProfileRow.key == key)).first()
        return (row.logo, row.updated_at) if row and row.logo else None

    def save(self, profiles: Iterable[VenueProfile]) -> int:
        """Upsert; a logo is converted only when its URL is new (the site's picture changed)."""
        by_key = {p.key: p for p in profiles}
        if not by_key:
            return 0
        with self.db.session(write=False) as session:
            known = dict(session.execute(select(VenueProfileRow.key, VenueProfileRow.logo_url).where(VenueProfileRow.logo.is_not(None))).all())
        now = utcnow()
        rows = []
        for key in sorted(by_key):  # key order: lock order
            p = by_key[key]
            row = {
                "key": key, "name": p.name, "source": p.source, "aliases": p.aliases or None, "about": p.about,
                "address": p.address, "latitude": p.latitude, "longitude": p.longitude, "links": p.links or None,
                "profile_url": p.profile_url, "updated_at": now,
            }  # fmt: skip
            if p.logo_url and known.get(key) != p.logo_url and p.logo and (webp := to_webp(p.logo, self.quality)):
                row |= {"logo_url": p.logo_url, "logo": webp}
            rows.append(row)

        def write() -> None:
            with self.db.session() as session:
                for row in rows:  # rows differ in columns (a logo or not): one statement each, ~80
                    stmt = dialect_insert(session, VenueProfileRow).values(row)
                    session.execute(stmt.on_conflict_do_update(
                        index_elements=["key"], set_={c: stmt.excluded[c] for c in row if c != "key"},
                    ))  # fmt: skip

        with_db_retry("venue profiles", write)
        return len(rows)


# ----------------------------------------------------------------- matching to our venue names
def _words(name: str) -> list[str]:
    text = unicodedata.normalize("NFKC", name).casefold()
    text = re.sub(r"['\"׳״`’]", "", text)  # צ'ילד = צילד
    return [w for w in re.split(r"[^\w]+", text) if w and w not in _GENERIC]


def _joined(name: str) -> str:
    return "".join(_words(name))


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    return math.hypot(math.radians(b[0] - a[0]), math.radians(b[1] - a[1]) * math.cos(lat)) * 6_371_000


def _fits(profile: VenueProfile, names: set[str], joined: str, where: tuple[float, float] | None) -> bool:
    here = (profile.latitude, profile.longitude)
    if where and None not in here and _distance_m(here, where) > FAR_M:  # type: ignore[arg-type]
        return False
    return joined in names


def match(
    profiles: list[VenueProfile], venues: dict[str, tuple[float, float] | None], linked: dict[str, str] | None = None
) -> dict[str, VenueProfile]:
    """Our venue name -> its profile, for the names that have one. ``venues``: name -> a spot of its events;
    ``linked``: name -> profile key, from the site's own events (``linked_names``)."""
    by_key = {p.key: p for p in profiles}
    out = {n: by_key[k] for n, k in (linked or {}).items() if k in by_key and n in venues}
    # A name left with under 3 letters ("חיפה 04" -> "04") says too little to be matched on its own.
    spellings = [(p, {j for s in (p.aliases or [p.name]) if len(j := _joined(s)) >= 3}) for p in profiles]
    for name, where in venues.items():
        if name in out or not (joined := _joined(name)):
            continue
        if found := next((p for p, names in spellings if _fits(p, names, joined, where)), None):
            out[name] = found
    return out


def linked_names(session: Session, names: Iterable[str] | None = None) -> dict[str, str]:
    """Venue name -> profile key, from upcoming events whose site named their venue's profile (the most
    events win when one name holds several)."""
    from partylinks.db import EventRow, upcoming_filter

    key = EventRow.extra[VENUE_PROFILE].astext
    query = select(EventRow.venue_name, key, func.count()).where(upcoming_filter(), key.is_not(None), EventRow.venue_name.is_not(None))
    if names is not None:
        query = query.where(EventRow.venue_name.in_(list(names)))
    out: dict[str, str] = {}
    for name, profile, _ in session.execute(query.group_by(EventRow.venue_name, key).order_by(func.count().desc())):
        out.setdefault(name, profile)
    return out


def profile_for(session: Session, profiles: list[VenueProfile], name: str) -> VenueProfile | None:
    """The profile of one venue page, placed by one of its events' coordinates."""
    from partylinks.db import EventRow

    spot = session.execute(
        select(EventRow.latitude, EventRow.longitude)
        .where(EventRow.venue_name == name, EventRow.latitude.is_not(None))
        .limit(1)
    ).first()  # fmt: skip
    where = (spot.latitude, spot.longitude) if spot else None
    return match(profiles, {name: where}, linked_names(session, [name])).get(name)


def logo_path(profile: VenueProfile) -> str | None:
    return f"/venue-logos/{profile.key}.webp" if profile.logo_url else None
