#!/usr/bin/env python3
"""
PartyLinks Chillz public-page scanner.

Discovers URLs from Chillz's public sitemap and extracts event metadata only
from pages the backend is permitted to access. It does not bypass Vercel
security challenges. HTTP 429/checkpoint responses are recorded and not retried
automatically.

Run:
    python chillz_scanner.py
Optional environment variables:
    CHILLZ_MAX_PAGES=100
    CHILLZ_CACHE_TTL_HOURS=6
    CHILLZ_OUTPUT=chillz_deals.json
"""

from __future__ import annotations

import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

BASE_URL = "https://chillz.com"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
CACHE_PATH = Path("chillz_scan_cache.json")
OUTPUT_PATH = Path(os.getenv("CHILLZ_OUTPUT", "chillz_deals.json"))
CACHE_TTL_SECONDS = int(float(os.getenv("CHILLZ_CACHE_TTL_HOURS", "6")) * 3600)
MAX_PAGES = int(os.getenv("CHILLZ_MAX_PAGES", "100"))
TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 3_000_000
USER_AGENT = "PartyLinksDealScanner/1.0 (authorized public-page metadata collection)"

FIELDS = ("event_name", "venue", "date", "price", "ticket_url", "last_seen")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        for item in value:
            found = clean(item)
            if found:
                return found
        return None
    if isinstance(value, dict):
        for key in ("name", "value", "price", "startDate", "address"):
            if key in value:
                found = clean(value[key])
                if found:
                    return found
        return None
    value = html.unescape(re.sub(r"<[^>]+>", " ", str(value)))
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch(url: str) -> tuple[int | None, str | None, bytes | None, dict[str, str]]:
    """Fetch once; caller decides whether/when to retry."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/ld+json,application/xml,text/xml,*/*;q=0.8",
    }
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES)
            return response.status, response.headers.get("Content-Type", ""), body, dict(response.headers.items())
    except HTTPError as exc:
        try:
            body = exc.read(200_000)
        except Exception:
            body = b""
        return exc.code, exc.headers.get("Content-Type", ""), body, dict(exc.headers.items())
    except (URLError, TimeoutError, OSError) as exc:
        return None, str(exc), None, {}


class HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self.in_title = False
        self.jsonld: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k.lower(): v for k, v in attrs if v is not None}
        if tag.lower() == "meta":
            key = (attrs_dict.get("property") or attrs_dict.get("name") or attrs_dict.get("itemprop") or "").lower()
            content = attrs_dict.get("content")
            if key and content:
                self.meta[key] = content
        elif tag.lower() == "title":
            self.in_title = True
        elif tag.lower() == "script" and attrs_dict.get("type", "").lower() == "application/ld+json":
            self._inside_jsonld = True
            self._jsonld_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False
        elif tag.lower() == "script" and getattr(self, "_inside_jsonld", False):
            self.jsonld.append("".join(getattr(self, "_jsonld_parts", [])))
            self._inside_jsonld = False
            self._jsonld_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if getattr(self, "_inside_jsonld", False):
            self._jsonld_parts.append(data)


def parse_jsonld(raw_blocks: list[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for raw in raw_blocks:
        try:
            walk(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return found


def event_from_page(url: str, body: bytes, content_type: str) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace")
    parser = HeadParser()
    if "html" in (content_type or "").lower() or "<html" in text[:1000].lower():
        try:
            parser.feed(text)
        except Exception:
            pass

    nodes = parse_jsonld(parser.jsonld)
    event_nodes = [
        node for node in nodes
        if any(t in str(node.get("@type", "")).lower()
               for t in ("event", "festival", "musicEvent".lower(), "theaterevent"))
    ]
    node = event_nodes[0] if event_nodes else {}

    graph_name = clean(node.get("name"))
    title = clean(parser.meta.get("og:title")) or clean(parser.meta.get("twitter:title")) or clean(" ".join(parser.title_parts))
    description = clean(parser.meta.get("og:description")) or clean(parser.meta.get("description"))
    venue_obj = node.get("location")
    venue = clean(venue_obj)
    if isinstance(venue_obj, dict):
        venue = clean(venue_obj.get("name")) or clean(venue_obj.get("address"))
    offers = node.get("offers")
    price = None
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if isinstance(offers, dict):
        amount = clean(offers.get("price"))
        currency = clean(offers.get("priceCurrency"))
        price = f"{amount} {currency}".strip() if amount else None
    price = price or clean(parser.meta.get("product:price:amount")) or clean(parser.meta.get("og:price:amount"))

    start_date = clean(node.get("startDate")) or clean(parser.meta.get("event:start_time"))
    ticket_url = clean(node.get("url")) or clean(parser.meta.get("og:url")) or url
    if isinstance(offers, dict):
        ticket_url = clean(offers.get("url")) or ticket_url

    # Avoid returning page-title noise as an event when no event signals exist.
    event_name = graph_name or title
    return {
        "event_name": event_name,
        "venue": venue,
        "date": start_date,
        "price": price,
        "ticket_url": ticket_url,
        "last_seen": utc_now(),
        "source_url": url,
        "description": description,
        "extraction": "json-ld" if graph_name else "open-graph/title",
    }


def discover_sitemap_urls(sitemap_url: str = SITEMAP_URL, seen: set[str] | None = None) -> list[str]:
    """Supports sitemap indexes and standard URL sets."""
    seen = seen or set()
    if sitemap_url in seen:
        return []
    seen.add(sitemap_url)

    status, content_type, body, _ = fetch(sitemap_url)
    if status != 200 or not body:
        print(f"[sitemap] unavailable: HTTP {status} ({sitemap_url})")
        return []

    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        print(f"[sitemap] invalid XML: {sitemap_url}")
        return []

    tag = root.tag.rsplit("}", 1)[-1].lower()
    locs = [
        (el.text or "").strip()
        for el in root.iter()
        if el.tag.rsplit("}", 1)[-1].lower() == "loc" and (el.text or "").strip()
    ]

    if tag == "sitemapindex":
        urls: list[str] = []
        for child in locs:
            parsed = urlparse(child)
            if parsed.scheme == "https" and parsed.netloc == "chillz.com":
                urls.extend(discover_sitemap_urls(child, seen))
        return urls

    urls = []
    for url in locs:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.lower() not in {"chillz.com", "www.chillz.com"}:
            continue
        if "/en/" in parsed.path and "/en/explore/" not in parsed.path:
            urls.append(url)
    return sorted(set(urls))


def retry_after_seconds(headers: dict[str, str]) -> int | None:
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    if not value:
        return None
    try:
        return max(0, int(value.strip()))
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            return max(0, int((target - datetime.now(target.tzinfo)).total_seconds()))
        except Exception:
            return None


def scan_url(url: str, cache: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    old = cache.get(url)
    if isinstance(old, dict) and now - old.get("checked_epoch", 0) < CACHE_TTL_SECONDS:
        cached = old.get("result")
        if isinstance(cached, dict):
            return cached

    status, content_type, body, headers = fetch(url)
    if status == 429:
        result = {
            "status": "blocked_by_checkpoint",
            "http_status": 429,
            "source_url": url,
            "retry_after_seconds": retry_after_seconds(headers),
            "last_seen": utc_now(),
        }
    elif status != 200 or body is None:
        result = {
            "status": "fetch_error",
            "http_status": status,
            "source_url": url,
            "error": content_type if status is None else None,
            "last_seen": utc_now(),
        }
    elif "html" not in (content_type or "").lower() and b"<html" not in body[:1000].lower():
        result = {
            "status": "not_html",
            "http_status": status,
            "source_url": url,
            "last_seen": utc_now(),
        }
    else:
        result = event_from_page(url, body, content_type or "")
        result["http_status"] = status
        result["status"] = "ok" if result.get("event_name") else "no_event_metadata"

        # A checkpoint HTML page can occasionally arrive with HTTP 200; detect it
        # and never interpret its title as an event.
        lowered = body[:200_000].decode("utf-8", errors="ignore").lower()
        if "vercel security checkpoint" in lowered:
            result = {
                "status": "blocked_by_checkpoint",
                "http_status": status,
                "source_url": url,
                "last_seen": utc_now(),
            }

    cache[url] = {"checked_epoch": now, "result": result}
    return result


def main() -> None:
    cache = load_json(CACHE_PATH, {})
    if not isinstance(cache, dict):
        cache = {}

    urls = discover_sitemap_urls()
    urls = urls[:MAX_PAGES]
    print(f"Discovered {len(urls)} event candidate URLs.")

    deals: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    blocked_count = 0

    for index, url in enumerate(urls, start=1):
        result = scan_url(url, cache)
        diagnostics.append(result)
        if result.get("status") == "blocked_by_checkpoint":
            blocked_count += 1
        elif result.get("status") == "ok":
            deals.append({field: result.get(field) for field in FIELDS} | {
                "source_url": result.get("source_url"),
                "description": result.get("description"),
                "extraction": result.get("extraction"),
            })

        print(f"[{index}/{len(urls)}] {result.get('status')}: {url}")

        # A conservative pause between page requests. Stop if the origin starts
        # returning checkpoint responses instead of increasing request pressure.
        if result.get("status") == "blocked_by_checkpoint":
            print("Checkpoint encountered; stopping to avoid repeated blocked requests.")
            break
        if index < len(urls):
            time.sleep(1.0)

    save_json(CACHE_PATH, cache)
    output = {
        "source": BASE_URL,
        "scanned_at": utc_now(),
        "discovered_urls": len(urls),
        "deals_count": len(deals),
        "blocked_count": blocked_count,
        "deals": deals,
        "diagnostics": diagnostics,
    }
    save_json(OUTPUT_PATH, output)
    print(f"\nSaved {len(deals)} deals to {OUTPUT_PATH}")
    print(f"Cache: {CACHE_PATH}")


if __name__ == "__main__":
    main()
