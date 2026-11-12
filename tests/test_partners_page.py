"""/partners server-renders the promotion page's sections (web/src/partners/texts.json) in Hebrew and English."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.partners_page import TEXTS_FILE
from tests.test_storage_api import settings_for


def test_partners_page_renders_every_section(tmp_path: Path) -> None:
    texts = json.loads(TEXTS_FILE.read_text(encoding="utf-8"))
    with TestClient(create_app(settings_for(tmp_path), start_scheduler=False)) as client:
        for lang, query in (("he", ""), ("en", "?lang=en")):
            res = client.get("/partners" + query)
            assert res.status_code == 200
            assert texts[lang]["metaTitle"] in res.text and '"FAQPage"' in res.text
            for section in texts[lang]["sections"]:
                assert f"<h2>{escape(section['h'])}</h2>" in res.text
            assert "contact@" not in res.text  # the address is revealed on the page by a click only
