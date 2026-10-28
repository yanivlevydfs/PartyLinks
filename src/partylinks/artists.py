"""What a ticket site tells about an artist (a curated profile). Stored by the artist dictionary,
``artistdb.py``; kept apart from it so models.py can import it without the database."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ArtistProfile:
    name: str
    source: str
    kind: str | None = None
    genre: str | None = None
    image_url: str | None = None
    spotify_url: str | None = None
    profile_url: str | None = None
    links: list[str] = field(default_factory=list)
