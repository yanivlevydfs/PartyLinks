"""Event artwork storage: downloads the images referenced by ``event_images``.

Storage is configurable: image bytes in the ``event_images.data`` column, or files
under ``<media.directory>/<source>/<sha1(url)>.<ext>``. Either way the API serves
them at ``/images/{id}``. Each source downloads with its own client after its own
run, so artwork fetching stays isolated per source like scraping.

Every stored file becomes two resized WebP copies, ``<file>.640.webp`` (cards) and
``<file>.1280.webp`` (the event page), served at ``/images/{id}/{width}.webp``, and the original
is deleted: ``/images/{id}`` then serves the 1280 copy. Only a picture that can't be converted
(animated, huge, unreadable) keeps its original. Copies are made when the file is stored (every
source goes through ``_store``); ``build_missing_variants`` converts the files stored before.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps
from sqlalchemy import or_, select

from partylinks.config import HttpSettings, MediaSettings, project_path
from partylinks.db import Database, EventRow, ImageRow, bulk_update_by_id, utcnow, with_db_retry

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
VARIANT_WIDTHS = (640, 1280)  # cards (296px wide on desktop, 166px at 3x on phones) / event page (420px, 2x-3x)
MAX_PIXELS = 50_000_000  # bigger pictures are not resized (the decode alone is ~200MB): the original is served
# MAX_PIXELS replaces PIL's own guard (warn past ~89M pixels, raise past ~179M): that one fires on open and
# filled the log, while make_variants reads only the header before the check below. PIL is used nowhere else.
Image.MAX_IMAGE_PIXELS = None
_ALPHA_MODES = ("RGBA", "LA", "PA")


def variant_path(relative: str, width: int) -> str:
    """``goout/ab12.jpg`` -> ``goout/ab12.jpg.640.webp``: named after the file, so shared files share copies."""
    return f"{relative}.{width}.webp"


def has_variants(path: Path) -> bool:
    return all(path.with_name(variant_path(path.name, w)).is_file() for w in VARIANT_WIDTHS)


def make_variants(path: Path, quality: int) -> bool:
    """Write the resized WebP copies of ``path`` beside it. Returns False (the original stays the
    only version) for animations, oversized or unreadable pictures; never raises."""
    try:
        with Image.open(path) as im:
            if getattr(im, "is_animated", False) or im.width * im.height > MAX_PIXELS:
                return False
            # A CMYK profile would be wrong once converted to RGB.
            icc = im.info.get("icc_profile") if im.mode in ("RGB", "RGBA") else None
            im.draft(im.mode, (VARIANT_WIDTHS[-1], VARIANT_WIDTHS[-1]))  # JPEG: decode at 1/2-1/8 scale, still >= 1280
            alpha = im.mode in _ALPHA_MODES or (im.mode == "P" and "transparency" in im.info)
            picture = ImageOps.exif_transpose(im).convert("RGBA" if alpha else "RGB")
        for width in VARIANT_WIDTHS:
            copy = picture.copy()
            copy.thumbnail((width, width * 4), Image.Resampling.LANCZOS)  # never upscales; tall posters stay whole
            target = path.with_name(variant_path(path.name, width))
            tmp = target.with_name(f"{target.name}.{os.getpid()}-{threading.get_ident()}.tmp")
            copy.save(tmp, "WEBP", quality=quality, method=4, icc_profile=icc)
            os.replace(tmp, target)  # atomic: a request never reads a half-written copy
        return True
    except Exception as exc:  # noqa: BLE001 - a broken picture keeps its original only
        log.warning("artwork copies of %s failed: %s: %s", path, type(exc).__name__, exc)
        return False


def webp_only(path: Path, quality: int) -> bool:
    """Make the WebP copies of ``path`` (unless they exist) and delete the original. A picture that
    can't be converted keeps its original and returns False."""
    if not (has_variants(path) or make_variants(path, quality)):
        return False
    path.unlink(missing_ok=True)
    return True


def _relative_path(source: str, url: str, content_type: str) -> str:
    """``<source>/<sha1(url)>.<ext>``: files are named by URL, so listings sharing a flyer share its file."""
    extension = mimetypes.guess_extension(content_type) or Path(httpx.URL(url).path).suffix or ".img"
    return f"{source}/{hashlib.sha1(url.encode()).hexdigest()}{extension}"


def _chunks[X](items: list[X], size: int) -> Iterable[list[X]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class MediaStore:
    def __init__(self, settings: MediaSettings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.root = project_path(settings.directory)
        self.retry_after = timedelta(hours=settings.retry_failed_after_hours)
        self._stop = threading.Event()
        # Resizing a big PNG takes ~200MB: at most this many at once across scrapes and the backfill.
        self._converting = threading.Semaphore(max(1, settings.variant_threads))

    def stop(self) -> None:
        """Make a running ``build_missing_variants`` return after its current files (shutdown)."""
        self._stop.set()

    def path_for(self, relative: str) -> Path:
        return self.root / relative

    def delete_files(self, relative_paths: Iterable[str | None]) -> int:
        """Delete artwork files no image row still references.

        Files are named by URL, so recurring flyers and per-promoter listings share one
        file; deleting it for one purged event must not break the others.
        """
        candidates: list[str] = sorted({p for p in relative_paths if p is not None and p})
        if not candidates:
            return 0
        with self.db.session(write=False) as session:
            still_used = {
                p
                for chunk in _chunks(candidates, 500)
                for p in session.scalars(select(ImageRow.local_path).where(ImageRow.local_path.in_(chunk)))
            }
        removed = 0
        for relative in (p for p in candidates if p not in still_used):
            try:
                self.path_for(relative).unlink(missing_ok=True)
                for width in VARIANT_WIDTHS:
                    self.path_for(variant_path(relative, width)).unlink(missing_ok=True)
                removed += 1
            except OSError as exc:
                log.warning("could not delete %s: %s", relative, exc)
        return removed

    def sweep_orphans(self, dry_run: bool = False, min_age_seconds: float = 3600) -> dict[str, Any]:
        """Delete every file under the media directory that no image row claims (its ``local_path``
        or one of that path's WebP copies): leftovers of crashes, earlier bugs or temp files.

        Guards: files modified in the last ``min_age_seconds`` are kept (a download writes its file
        before its row is saved; copies and temp files are being written), and nothing is deleted
        when the image table returns no stored file at all, so a DB problem can't wipe the folder.
        A failing query raises before anything is touched."""
        result: dict[str, Any] = {"dry_run": dry_run, "files": 0, "bytes": 0, "skipped": None}
        if not self.root.is_dir():
            return result
        with self.db.session(write=False) as session:
            claimed_paths = {p for p in session.scalars(select(ImageRow.local_path).where(ImageRow.local_path.is_not(None))) if p is not None}
        if not claimed_paths:
            result["skipped"] = "no stored image rows: refusing to sweep"
            log.warning("artwork sweep: %s", result["skipped"])
            return result
        claimed = claimed_paths | {variant_path(p, w) for p in claimed_paths for w in VARIANT_WIDTHS}
        cutoff = time.time() - min_age_seconds
        for directory, _, names in os.walk(self.root):
            for name in names:
                path = Path(directory) / name
                if path.relative_to(self.root).as_posix() in claimed:
                    continue
                try:
                    stat = path.stat()
                    if stat.st_mtime > cutoff:  # being written, or its row not saved yet
                        continue
                    if not dry_run:
                        path.unlink()
                except FileNotFoundError:  # a temp copy renamed into place meanwhile
                    continue
                except OSError as exc:
                    log.warning("artwork sweep: could not remove %s: %s", path, exc)
                    continue
                result["files"] += 1
                result["bytes"] += stat.st_size
        log.info(
            "artwork sweep%s: %d orphan files, %.1f MB %s",
            " (dry run)" if dry_run else "", result["files"], result["bytes"] / 1e6, "found" if dry_run else "removed",
        )
        return result

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
                await asyncio.to_thread(with_db_retry, f"{source} artwork", lambda s=stored, f=failed: self._persist(s, f))
                stored_count, failed_count = stored_count + len(stored), failed_count + len(failed)
        log.info("%s artwork: %d stored, %d failed", source, stored_count, failed_count)
        return stored_count, failed_count

    def _pending(self, source: str) -> list[tuple[int, str]]:
        with self.db.session(write=False) as session:  # a read: never queue behind the writers
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
            row["local_path"] = _relative_path(source, url, content_type)
            await asyncio.to_thread(self._store, row["local_path"], data)  # off the event loop: resizing is CPU
        return row

    def _store(self, relative: str, data: bytes) -> None:
        """Every downloaded artwork file is stored here: the file, then its WebP copies, then the
        original is deleted (kept only when it can't be converted)."""
        target = self._write(relative, data)
        if has_variants(target):  # files are named by URL: the same flyer again already has them
            target.unlink(missing_ok=True)
            return
        with self._converting:
            webp_only(target, self.settings.webp_quality)

    def _write(self, relative: str, data: bytes) -> Path:
        target = self.path_for(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def build_missing_variants(self, threads: int | None = None) -> tuple[int, int]:
        """Convert every stored original still on disk (files from before WebP-only storage): make
        its copies if missing, then delete it, ``threads`` at a time. Resumable: converted files
        are gone, so a restart carries on. Returns (converted, failed). ponytail: animations and broken files are retried at each
        start (cheap, they fail on open); keep a skip list if that ever shows in the logs."""
        threads = self.settings.variant_threads if threads is None else threads
        if self.settings.storage != "filesystem" or threads < 1:
            return 0, 0
        with self.db.session(write=False) as session:
            relatives = [
                r for r in session.scalars(select(ImageRow.local_path).where(ImageRow.local_path.is_not(None)).distinct())
                if r is not None
            ]
        todo = [p for p in (self.path_for(r) for r in relatives) if p.is_file()]
        if not todo:
            return 0, 0
        log.info("artwork copies: %d of %d files still have their original, %d threads", len(todo), len(relatives), threads)
        made = failed = 0
        started = time.monotonic()

        def one(path: Path) -> bool | None:
            if self._stop.is_set():
                return None
            if has_variants(path):  # converted earlier, original not deleted yet: no decode needed
                return webp_only(path, self.settings.webp_quality)
            with self._converting:
                return webp_only(path, self.settings.webp_quality)

        with ThreadPoolExecutor(threads, thread_name_prefix="artwork-copies") as pool:
            for done, ok in enumerate(pool.map(one, todo), 1):
                if ok is not None:
                    made, failed = made + ok, failed + (not ok)
                if done % 500 == 0:
                    log.info("artwork copies: %d/%d (%d kept the original only), %.0fs", done, len(todo), failed, time.monotonic() - started)
        log.info(
            "artwork copies: %s, %d converted (original deleted), %d kept the original only (animated, huge or unreadable), %.0fs",
            "stopped" if self._stop.is_set() else "done", made, failed, time.monotonic() - started,
        )
        return made, failed

    def move_to_files(self, batch: int = 200, reclaim_every: int = 10) -> int:
        """With ``storage: filesystem``, move artwork still kept in the database to files, a batch at
        a time (each file is written before its BLOB is cleared, so a restart resumes where it
        stopped), and give the freed pages back to the disk as it goes. Returns the images moved."""
        if self.settings.storage != "filesystem":
            return 0
        moved = batches = 0
        while True:
            with self.db.session() as session:
                rows = session.execute(
                    select(ImageRow.id, ImageRow.url, ImageRow.content_type, ImageRow.data, EventRow.source)
                    .join(EventRow)
                    .where(ImageRow.data.is_not(None))
                    .limit(batch)
                ).all()
                if not rows:
                    break
                updates: list[dict[str, Any]] = []
                written: set[str] = set()
                for row in rows:
                    relative = _relative_path(row.source, row.url, row.content_type or "")
                    self._write(relative, row.data)
                    updates.append({"id": row.id, "local_path": relative, "data": None})
                    written.add(relative)
                bulk_update_by_id(session, ImageRow, updates)
            for relative in written:  # after the commit: no write lock held while resizing
                webp_only(self.path_for(relative), self.settings.webp_quality)
            moved, batches = moved + len(rows), batches + 1
            if batches % reclaim_every == 0:
                log.info("artwork: %d images moved from the database to files", moved)
                self.db.reclaim_space()
        if moved:
            self.db.reclaim_space()
            log.info("artwork: done, %d images moved from the database to files", moved)
        return moved

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
