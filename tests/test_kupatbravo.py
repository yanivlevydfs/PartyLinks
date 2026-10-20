"""Kupat Bravo parser test against a trimmed real show page and sitemap."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import kupatbravo

FIXTURES = Path(__file__).parent / "fixtures" / "kupatbravo"
URL = "https://kupatbravo.co.il/announce/85348"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_kupatbravo_sitemap() -> None:
    urls = kupatbravo.sitemap_show_urls(_read("sitemap.xml"), "/announce/")
    assert len(urls) == 2 and all("/announce/" in u for u in urls)


def test_kupatbravo_show() -> None:
    ashdod, beersheba = kupatbravo.parse_show(_read("show.html"), URL, "Asia/Jerusalem")
    assert ashdod.source_event_id == "204032" and str(ashdod.url) == URL
    assert ashdod.title.startswith("היפהפייה הנרדמת")
    assert ashdod.start_at == datetime(2026, 10, 20, 17, 30, tzinfo=ZoneInfo("Asia/Jerusalem"))
    assert (ashdod.venue_name, ashdod.address, ashdod.city) == ("המשכן לאמנויות הבמה אשדוד", "דרך ארץ 1", "אשדוד")
    # The site swaps latitude and longitude.
    assert (ashdod.latitude, ashdod.longitude) == (31.79299, 34.636347)
    assert (ashdod.min_price, ashdod.max_price) == (Decimal(175), Decimal(365)) and not ashdod.is_sold_out
    assert ashdod.source_genres == ["הופעות מחול ובלט"] and ashdod.organizer == "פ.ג.ק. הפקות"
    assert ashdod.image_url == "https://katalog.co.il/show/image/85348.jpg?v=4"
    assert ashdod.description and "תיאטרון הבלט" in ashdod.description
    assert beersheba.city == "באר שבע" and beersheba.source_event_id == "204033"
