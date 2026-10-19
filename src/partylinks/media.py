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
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select, update

from partylinks.config import HttpSettings, MediaSettings, project_path
from partylinks.db import Database, EventRow, ImageRow, utcnow

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


class MediaStore:
    def __init__(self, settings: MediaSettings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.root = project_path(settings.directory)

    def path_for(self, relative: str) -> Path:
        return self.root / relative

    def delete_files(self, relative_paths: Iterable[str | None]) -> int:
        removed = 0
        for relative in filter(None, relative_paths):
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
        async with httpx.AsyncClient(
            timeout=http.timeout_seconds, follow_redirects=True, headers={"User-Agent": http.user_agent}
        ) as client:
            results = await asyncio.gather(
                *(self._download(client, semaphore, source, image_id, url) for image_id, url in pending)
            )
        stored = [r for r in results if "error" not in r]
        failed = [r for r in results if "error" in r]
        await asyncio.to_thread(self._persist, stored, failed)
        log.info("%s artwork: %d stored, %d failed", source, len(stored), len(failed))
        return len(stored), len(failed)

    def _pending(self, source: str) -> list[tuple[int, str]]:
        with self.db.session() as session:
            rows = session.execute(
                select(ImageRow.id, ImageRow.url)
                .join(EventRow)
                .where(EventRow.source == source, ImageRow.downloaded_at.is_(None), ImageRow.error.is_(None))
            )
            return [(row.id, row.url) for row in rows]

    async def _download(
        self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore, source: str, image_id: int, url: str
    ) -> dict[str, Any]:
        async with semaphore:
            try:
                data, content_type = await self._fetch(client, url)
            except Exception as exc:  # noqa: BLE001 - record and continue with other images
                return {"id": image_id, "error": f"{type(exc).__name__}: {exc}"[:500]}
        row: dict[str, Any] = {
            "id": image_id, "content_type": content_type, "size_bytes": len(data), "downloaded_at": utcnow(),
            "data": None, "local_path": None,
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
        allowed = self.settings.allowed_content_types
        if allowed and content_type not in allowed:
            raise ValueError(f"not an image (declared {declared!r})")
        return data, content_type

    def _persist(self, stored: list[dict[str, Any]], failed: list[dict[str, Any]]) -> None:
        """Bulk UPDATE by primary key; images whose event was purged meanwhile are skipped."""
        with self.db.session() as session:
            alive = set(session.scalars(select(ImageRow.id).where(ImageRow.id.in_([r["id"] for r in stored + failed]))))
            orphans = [r["local_path"] for r in stored if r["id"] not in alive]
            if rows := [r for r in stored if r["id"] in alive]:
                session.execute(update(ImageRow), rows)
            if rows := [r for r in failed if r["id"] in alive]:
                session.execute(update(ImageRow), rows)
        self.delete_files(orphans)
