"""The home page's menu in the HTML crawlers read first, and the accessibility statement's addresses."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.seo import SECTIONS
from tests.test_storage_api import settings_for


def test_the_home_page_links_every_main_section_by_name(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path), start_scheduler=False)) as c:
        he, en = c.get("/").text, c.get("/?lang=en").text
        for path, name_he, name_en in SECTIONS:
            assert f'<a href="{path}">{name_he}</a>' in he and f'<a href="{path}">{name_en}</a>' in en
            assert c.get(path).status_code == 200, path  # no menu link to a missing page
        sitemap = c.get("/sitemap-1.xml").text  # /sitemap.xml is the index of these
        assert "/accessibility</loc>" in sitemap and "accessibility-statement" not in sitemap  # never a redirect


def test_the_accessibility_statement_answers_at_its_other_name(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path), start_scheduler=False)) as c:
        moved = c.get("/accessibility-statement", follow_redirects=False)
        assert moved.status_code == 301 and moved.headers["location"] == "/accessibility"
        assert '<a href="/contact">' in c.get("/accessibility").text
