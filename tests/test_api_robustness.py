"""Every GET route, asked with hostile or nonsense input, answers 4xx or 2xx, never 500, and quickly.

The routes are enumerated from the app, so a route added later is fuzzed without touching this file.
The SEO pages are then rendered over a realistic mix of events and their HTML / XML / JSON-LD parsed.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import SecretStr

from partylinks.api import create_app
from partylinks.config import Settings
from partylinks.models import TicketTier
from tests.test_storage_api import event, seed, settings_for

SLOW_S = 5.0  # a request on a handful of events taking longer is a hang, not work
GARBAGE = ["", "-1", "0", "99999999999999999999", "1e308", "nan", "x" * 5000, "שלום עולם", "🎉🔥",
           "' OR 1=1--", "<script>alert(1)</script>", "\x00", "%00", "../../etc/passwd", "2026-13-45"]  # fmt: skip
BAD_PATH = ["x" * 3000, "..", "%00", "%2e%2e%2f", "🎉", "שלום", "' OR '1'='1", "%25", "_", "nope:nope"]
ADMIN = {"authorization": "Bearer s3cret"}
SKIP = {"/go/{event_id:path}"}  # a redirect counted per click (test_security covers it); never a 500 below either


def _settings(tmp_path: Path) -> Settings:
    settings = settings_for(tmp_path)
    settings.admin.token = SecretStr("s3cret")
    settings.security.rate_limit_per_minute = settings.security.data_rate_limit_per_minute = 1_000_000
    settings.security.admin_rate_limit_per_minute = settings.security.ban_after_strikes = 1_000_000
    settings.security.go_rate_limit_per_minute = 1_000_000
    return settings


def _mixed_events() -> list[Any]:
    now = datetime.now(UTC)
    return [
        event("plain", now + timedelta(days=1), venue_name="Gagarin", lineup=["DJ Shadow", "Nina Kraviz"], genres=["techno"]),
        event("free", now + timedelta(hours=3), tickets=[TicketTier(name="Free", price=Decimal(0))], venue_name="Barby"),
        event("no-price", now + timedelta(days=2), tickets=[], images=[], description=None),
        event("no-end", now + timedelta(days=3), end_at=None, latitude=32.08, longitude=34.78),
        event("abroad", now + timedelta(days=10), address="Berlin, Germany", city="Berlin", country="DE",
              latitude=52.52, longitude=13.40, lineup=["Ben Klock"]),  # fmt: skip
        event("weird", now + timedelta(days=4), title='<b>"Quotes" & </script> שלום 🎉', venue_name="100% _club_",
              description="</script><script>alert(1)</script>"),  # fmt: skip
        event("past", now - timedelta(days=3), end_at=now - timedelta(days=3) + timedelta(hours=4)),
    ]


def _get_routes(app: Any) -> list[APIRoute]:
    routes = [r for top in app.routes for r in getattr(getattr(top, "original_router", None), "routes", [top])]
    return [r for r in routes if isinstance(r, APIRoute) and "GET" in (r.methods or ()) and r.path not in SKIP]


def _fill(path: str, value: str) -> str:
    return re.sub(r"\{[^}]+\}", quote(value, safe="%") if "%" in value else quote(value, safe=""), path)


def _check(c: TestClient, url: str, failures: list[str], **kw: Any) -> None:
    started = time.perf_counter()
    got = c.get(url, headers=ADMIN, follow_redirects=False, **kw)
    took = time.perf_counter() - started
    if got.status_code >= 500 or took > SLOW_S:
        failures.append(f"{url} {kw.get('params', '')!r:.80} -> {got.status_code} in {took:.1f}s")


def test_every_get_route_survives_hostile_input(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    seed(settings, _mixed_events())
    app = create_app(settings, start_scheduler=False)
    failures: list[str] = []
    with TestClient(app, raise_server_exceptions=False, client=("198.51.100.77", 1)) as c:
        routes = _get_routes(app)
        assert len(routes) > 40  # the enumeration really found the routes
        for route in routes:
            has_path = "{" in route.path
            for value in BAD_PATH if has_path else ["x"]:
                url = _fill(route.path, value)
                _check(c, url, failures)
                for param in route.dependant.query_params:
                    for junk in GARBAGE:
                        _check(c, url, failures, params={param.name: junk})
                    _check(c, url, failures, params={param.name: ["a"] * 300})  # a list param repeated
                if not has_path:
                    break
            if has_path:  # the path params themselves with garbage
                for junk in GARBAGE:
                    _check(c, _fill(route.path, junk), failures)
    assert not failures, "\n".join(failures)


LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def _valid_page(got: Any) -> list[Any]:
    html = got.text
    assert html.lstrip().lower().startswith("<!doctype html")
    return [json.loads(block) for block in LD.findall(html)]  # every JSON-LD block parses


def test_seo_pages_render_valid_markup(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    seed(settings, _mixed_events())
    app = create_app(settings, start_scheduler=False)
    with TestClient(app, raise_server_exceptions=False, client=("198.51.100.78", 1)) as c:
        found = c.get("/events", params={"size": 50}).json()["items"]
        slugs = {e["id"]: e.get("slug") or e["id"] for e in found}
        assert "test:past" not in slugs  # never a past event
        for ref in slugs.values():
            got = c.get(f"/event/{quote(ref, safe='')}")
            assert got.status_code == 200, ref
            assert any(str(ld.get("@type", "")).endswith("Event") for ld in _valid_page(got)), ref
        assert c.get("/event/test:past").status_code == 410
        assert c.get("/event/never-was").status_code == 410

        for path in ("/", "/?lang=en", "/tonight", "/weekend", "/genre/techno", "/city/tel-aviv", "/venue/Gagarin",
                     "/artist/DJ%20Shadow", "/venues", "/artists", "/map", "/abroad", "/blog", "/privacy", "/terms"):  # fmt: skip
            got = c.get(path)
            assert got.status_code in (200, 404), path
            _valid_page(got)
        # Share links' tracking tags reuse the cached page; only the language makes another.
        pages = len(app.state.html_cache._items)
        for query in ("fbclid=abc", "utm_source=whatsapp&utm_medium=share", "fbclid=xyz&lang=he"):
            assert c.get(f"/tonight?{query}").status_code == 200
        assert len(app.state.html_cache._items) == pages
        assert 'lang="en"' in c.get("/tonight?fbclid=1&lang=en").text
        for path in ("/genre/no-such-genre", "/city/atlantis"):
            assert c.get(path).status_code == 404, path

        # LIKE wildcards in a name are taken literally: "%" is not every venue.
        assert "Techno night plain" not in c.get("/venue/%25").text
        assert "100% _club_" in c.get("/venue/" + quote("100% _club_", safe="")).text

        sitemap = c.get("/sitemap.xml")
        assert sitemap.status_code == 200
        ElementTree.fromstring(sitemap.content)
        for loc in re.findall(r"<loc>([^<]+)</loc>", sitemap.text):
            part = loc.split("/")[-1]
            assert (got := c.get(f"/{part}")).status_code == 200, part
            ElementTree.fromstring(got.content)
        assert c.get("/sitemap-999.xml").status_code == 404
        assert c.get("/robots.txt").status_code == 200 and "Sitemap:" in c.get("/robots.txt").text
        assert c.get("/llms.txt").status_code == 200
        assert c.get("/blog/rss.xml").status_code == 200 and ElementTree.fromstring(c.get("/blog/rss.xml").content) is not None
        posts = c.get("/posts").json()
        for post in (posts if isinstance(posts, list) else posts.get("items", []))[:5]:
            got = c.get(f"/blog/{post['slug']}")
            assert got.status_code == 200
            _valid_page(got)
        assert c.get("/blog/no-such-post").status_code == 404
