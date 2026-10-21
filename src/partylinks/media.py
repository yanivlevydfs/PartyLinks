"""Event artwork storage: downloads the images referenced by ``event_images``.

Storage is configurable: image bytes in the ``event_images.data`` column, or files
under ``<media.directory>/<source>/<sha1(url)>.<ext>``. Either way the API serves
them at ``/images/{id}``. Each source downloads with its own client after its own
run, so artwork fetching stays isolated per source like scraping.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import or_, select

from partylinks.config import HttpSettings, MediaSettings, project_path
from partylinks.db import Database, EventRow, ImageRow, bulk_update_by_id, utcnow

log = logging.getLogger(__name__)


_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_image_type(data: bytes) -> str | None:
    """Detect the image format from its magic bytes."""
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    return None


PERSIST_CHUNK = 50


def _chunks[X](items: list[X], size: int) -> Iterable[list[X]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class MediaStore:
    def __init__(self, settings: MediaSettings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.root = project_path(settings.directory)
        self.retry_after = timedelta(hours=settings.retry_failed_after_hours)

    def path_for(self, relative: str) -> Path:
        return self.root / relative

    def delete_files(self, relative_paths: Iterable[str | None]) -> int:
        """Delete artwork files no image row still references.

        Files are named by URL, so recurring flyers and per-promoter listings share one
        file; deleting it for one purged event must not break the others.
        """
        candidates = sorted({p for p in relative_paths if p})
        if not candidates:
            return 0
        with self.db.session() as session:
            still_used = {
                p
                for chunk in _chunks(candidates, 500)
                for p in session.scalars(select(ImageRow.local_path).where(ImageRow.local_path.in_(chunk)))
            }
        removed = 0
        for relative in (p for p in candidates if p not in still_used):
            try:
                self.path_for(relative).unlink(missing_ok=True)
                removed += 1
            except OSError as exc:
                log.warning("could not delete %s: %s", relative, exc)
        return removed

    async def download_pending(self, source: str, http: HttpSettings) -> tuple[int, int]:
        """Download every not-yet-stored image of ``source`` in parallel, then persist
        all results with bulk updates. Returns (stored, failed)."""
        pending = await asyncio.to_thread(self._pending, source)
        if not pending:
            return 0, 0
        semaphore = asyncio.Semaphore(http.concurrency)
        stored_count = failed_count = 0
        async with httpx.AsyncClient(
            timeout=http.timeout_seconds, follow_redirects=True, proxy=http.proxy, headers={"User-Agent": http.user_agent}
        ) as client:
            # Chunks bound memory (BLOBs) and SQL parameters, and keep what finished if a later chunk fails.
            for chunk in _chunks(pending, PERSIST_CHUNK):
                results = await asyncio.gather(
                    *(self._download(client, semaphore, source, image_id, url) for image_id, url in chunk)
                )
                # Success rows carry error=None (to clear an earlier failure), so test the value.
                stored = [r for r in results if r.get("error") is None]
                failed = [r for r in results if r.get("error") is not None]
                await asyncio.to_thread(self._persist, stored, failed)
                stored_count, failed_count = stored_count + len(stored), failed_count + len(failed)
        log.info("%s artwork: %d stored, %d failed", source, stored_count, failed_count)
        return stored_count, failed_count

    def _pending(self, source: str) -> list[tuple[int, str]]:
        with self.db.session() as session:
            rows = session.execute(
                select(ImageRow.id, ImageRow.url)
                .join(EventRow)
                .where(
                    EventRow.source == source,
                    ImageRow.downloaded_at.is_(None),
                    # Transient failures (timeout, 503) are retried after a cooldown.
                    or_(ImageRow.error_at.is_(None), ImageRow.error_at < utcnow() - self.retry_after),
                )
            )
            return [(row.id, row.url) for row in rows]

    async def _download(
        self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore, source: str, image_id: int, url: str
    ) -> dict[str, Any]:
        async with semaphore:
            try:
                data, content_type = await self._fetch(client, url)
            except Exception as exc:  # noqa: BLE001 - record and continue with other images
                return {"id": image_id, "error": f"{type(exc).__name__}: {exc}"[:500], "error_at": utcnow()}
        row: dict[str, Any] = {
            "id": image_id, "content_type": content_type, "size_bytes": len(data), "downloaded_at": utcnow(),
            "data": None, "local_path": None, "error": None, "error_at": None,
        }  # fmt: skip
        if self.settings.storage == "database":
            row["data"] = data
        else:
            extension = mimetypes.guess_extension(content_type) or Path(httpx.URL(url).path).suffix or ".img"
            row["local_path"] = f"{source}/{hashlib.sha1(url.encode()).hexdigest()}{extension}"
            target = self.path_for(row["local_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_bytes, data)
        return row

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            declared = response.headers.get("content-type", "").split(";")[0].strip().lower()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self.settings.max_bytes:
                    raise ValueError(f"image larger than {self.settings.max_bytes} bytes")
                chunks.append(chunk)
        data = b"".join(chunks)
        # CDNs often mislabel images (application/octet-stream, image/jpg): trust the bytes.
        content_type = sniff_image_type(data) or declared
        if content_type not in self.settings.allowed_content_types:
            raise ValueError(f"not an image (declared {declared!r})")
        return data, content_type

    def _persist(self, stored: list[dict[str, Any]], failed: list[dict[str, Any]]) -> None:
        """Bulk UPDATE by primary key; images whose event was purged meanwhile are skipped."""
        with self.db.session() as session:
            # Core executemany: an image whose event was purged meanwhile is simply not updated.
            bulk_update_by_id(session, ImageRow, stored)
            bulk_update_by_id(session, ImageRow, failed)
