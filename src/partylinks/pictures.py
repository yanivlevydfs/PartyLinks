"""Which picture an event shows, everywhere (cards, event page, JSON-LD, mails).

The owner's chain (2026-11-11): the event's own stored picture, else a duplicate listing's stored
picture, else its own original URL while that may still load, else the drawn card. A picture that
failed to download (403, 404, "not an image") or a site logo / favicon / placeholder is no picture:
Berry Sakharof's page (tickchak) had none because its only "picture" was ticketmaster's favicon,
though 11 duplicates of it had real ones; 476 events were in the same state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from partylinks.parsing import is_site_logo

if TYPE_CHECKING:
    from partylinks.db import EventRow, ImageRow


def usable(image: ImageRow) -> bool:
    """Stored, or still waiting for its download; never a failed one or a site logo."""
    return not is_site_logo(image.url) and (image.downloaded_at is not None or image.error is None)


def live_images(row: EventRow) -> list[ImageRow]:
    return [i for i in row.images if usable(i)]


def live_image_url(row: EventRow) -> str | None:
    """The source's own picture URL, unless it is a logo or its download failed."""
    url = row.image_url
    if not url or is_site_logo(url):
        return None
    failed = any(i.url == url and i.downloaded_at is None and i.error is not None for i in row.images)
    return None if failed else url
