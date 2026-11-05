"""EfoKulam (efokulam.co.il, "איפה כולם") - a nightlife guide: club nights, parties and festivals, mostly Tel Aviv.

A WordPress site whose ``events`` post type is public in wp-json; this source reads it the way the page's
own REST links do:
- ``/wp-json/wp/v2/event-type`` (one request): the site's music categories (טכנו, מיינסטרים, היפ הופ...),
  id -> name, kept as source genres. About half the events carry none; those get ``base_genre`` (a party).
- ``/wp-json/wp/v2/events`` in pages of 100 with the featured image embedded (~5 requests, ~460 rows):
  ``meta`` gives the date (``event-date``: the local wall clock written as a UTC epoch, so 23:00 reads
  23:00 UTC), the start (``event-gate_open``), a Google-formatted address and the seller's link
  (``ticket_link``: Go-Out, Eventer...), kept as ``extra["purchase_url"]`` so dedup merges the event with
  that seller's own listing. REST has no date filter, so past rows are dropped from the listing by their
  ``event-date`` and ``is_current`` (~50 current events); no event page is fetched.
Prices: the article (``content``) opens with a row of chips; the highlighted ones (``price_chip_selector``)
state the entry: "החל מ-₪80", "מוקדמות 60 ₪ · רגיל 70 ₪", "הרשמה חינם עד 00:00" (a free tier), or a drink deal
("בקבוק אבסולוט 99 ₪", skipped by ``drink_words``). Each "·" / "/" part is one tier; ~2/3 of current events have one.
Artists: a "ליינאפ" label box (``lineup_labels``) opens the lineup; its first chip row or list (and name-only <h3>
headings before it) gives the names, "לפני ואחרי: A, B" split, placeholders (``lineup_skip``) dropped; ~1/3 of events.
The site has no venue field: the venue is the address's first part only when the address reads
"<place>, <street>, <city>"; else the venue pass names it from its spot. The address is geocoded (no coordinates on the site).
robots.txt disallows only /wp-admin/.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.countries import country_code
from partylinks.enrichment import UNKNOWN_COUNTRY
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, to_decimal, to_int, zone
from partylinks.retention import is_current
from partylinks.scrapers.abroad import local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

# ponytail: retention's defaults (config/settings.yaml uses the same); pass Settings.retention in if they ever differ.
_RETENTION = RetentionSettings()
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})")
# "80 ₪", "₪80", "80–120 ₪"; a number right after ":" or a digit is a clock ("00:00 – 60 ₪" is 60).
_PRICE = re.compile(r"(?<![\d:.])(\d+(?:\.\d+)?)(?:[–-](\d+(?:\.\d+)?))?\s*₪|₪\s*(\d+(?:\.\d+)?)")
# What is left of "מחיר: החל מ-₪80" / "כרטיסים מ-60 ₪" once the price is cut out.
_FROM = re.compile(r"^מחיר:?|(?:^|\s)(?:החל\s*)?מ[-־‑]?$")


class EfoKulamSettings(SourceSettings):
    site_url: str = "https://efokulam.co.il"
    events_api_path: str = "/wp-json/wp/v2/events"
    types_api_path: str = "/wp-json/wp/v2/event-type"
    per_page: int = 100  # the REST API maximum
    max_pages: int = Field(50, ge=1)
    # Events without a category on a nightlife guide are club nights: this declared genre maps to nightclubs.
    base_genre: str = "מסיבה"
    # Title prefixes left over from the seller's "duplicate event" button.
    title_prefixes: list[str] = Field(default_factory=lambda: ["Copy of "])
    # Address parts that are a street, a PO box or a region, never a venue name.
    street_words: list[str] = Field(
        default_factory=lambda: ["רחוב", "רח'", "שדרות", "שד'", "דרך", "כיכר", "ת.ד", "מחוז", "צומת"]
    )
    po_box: str = "ת.ד"
    # The highlighted chips at the top of an article: the entry price.
    price_chip_selector: str = 'span[style*="#FFB550"]'
    # A highlighted chip part with one of these is a drink deal, not an entry price.
    drink_words: list[str] = Field(
        default_factory=lambda: ["בקבוק", "אלכוהול", "שתייה", "ייסר", "קוקטייל", "דרינק", "טקילה"]
    )
    free_word: str = "חינם"
    # A short label box that opens the article's lineup block ("ליינאפ"); the first chip row / list after it
    # (and any name-only <h3> before it) is the lineup.
    lineup_labels: list[str] = Field(default_factory=lambda: ["ליינאפ", "הליינאפ", "ליינאפ מלא", "lineup", "line up"])
    # Lineup chips that are no one: a placeholder guest or a vague "live shows" item.
    lineup_skip: list[str] = Field(
        default_factory=lambda: ["אורח מיוחד", "אורחת מיוחדת", "הופעות חיות מיוחדות", "ועוד", "ועוד אורחים"]
    )


class EfoKulamScraper(BaseScraper[EfoKulamSettings]):
    name = "efokulam"
    settings_model = EfoKulamSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        terms = await self.get_json(
            s.site_url + s.types_api_path, params={"per_page": s.per_page, "_fields": "id,name"}
        )
        types = {t["id"]: html.unescape(str(t["name"])) for t in terms if isinstance(t, dict) and t.get("id")}

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {"per_page": s.per_page, "page": index + 1, "_embed": "wp:featuredmedia",
                      "_fields": "id,link,title,content,meta,event-type,_links,_embedded"}  # fmt: skip
            response = await self.request("GET", s.site_url + s.events_api_path, params=params)
            rows = response.json()
            if not isinstance(rows, list):
                raise ParseError(f"{response.request.url}: expected a list of events")
            # The total is required: asking past the last page is an error, not an empty page.
            return [r for r in rows if isinstance(r, dict)], response.headers.get("X-WP-Total")

        rows = await self.paginate(page, s.per_page, s.max_pages)
        self.log.debug("listed %d events", len(rows))
        for row in rows:
            try:
                event = parse_event(row, types, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                self.record_failure(f"event {row.get('id')}", exc)
                continue
            if event and is_current(
                event.start_at, event.end_at, _RETENTION, self.timezone, time_known=event.time_known
            ):
                yield event


def _is_city(venue: str, city: str) -> bool:
    """A "venue" that is only the town's name ("תל אביב" before "תל אביב-יפו")."""
    squash = lambda text: "".join(ch for ch in text if ch.isalnum() and ch not in "וי")
    return squash(city).startswith(squash(venue))


def split_address(address: str | None, s: EfoKulamSettings) -> tuple[str | None, str | None, str | None]:
    """(venue, address without the PO box, ISO country when abroad) of a Google-formatted address."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip() and not p.strip().startswith(s.po_box)]
    if not parts:
        return None, None, None
    code = country_code(parts[-1])
    local = parts[:-1] if code != UNKNOWN_COUNTRY else parts
    first = local[0].split("|")[0].strip() if local else ""
    # "<place>, <street>, <city>" only: "הלוחמים, תל אביב-יפו" is a street, "האנגר 23" an address.
    venue = (
        first
        if len(local) >= 3
        and not any(ch.isdigit() for ch in first)
        and not any(first.startswith(w) for w in s.street_words)
        and not _is_city(first, local[-1])
        else None
    )
    return venue, ", ".join(parts), code if code not in (UNKNOWN_COUNTRY, "IL") else None


def _title(row: dict[str, Any], s: EfoKulamSettings) -> str | None:
    title = clean_str(html.unescape(str((row.get("title") or {}).get("rendered") or "")))
    for prefix in s.title_prefixes:
        if title and title.lower().startswith(prefix.lower()):
            title = clean_str(title[len(prefix) :])
    return title


def _image(row: dict[str, Any]) -> list[str]:
    media = (row.get("_embedded") or {}).get("wp:featuredmedia") or []
    url = clean_str(media[0].get("source_url")) if media and isinstance(media[0], dict) else None
    return [url] if url and url.startswith("http") else []


def parse_tickets(content: str | None, s: EfoKulamSettings) -> list[TicketTier]:
    """Ticket tiers stated by the article's highlighted chips."""
    if not content:
        return []
    tiers: list[TicketTier] = []
    for chip in LexborHTMLParser(content).css(s.price_chip_selector):
        for part in re.split(r"[·/]", chip.text()):
            part = " ".join(part.split())
            if not part or any(w in part for w in s.drink_words):
                continue
            prices = [to_decimal(v) for m in _PRICE.finditer(part) for v in m.groups() if v]
            label = _FROM.sub("", " ".join(_PRICE.sub("", part).split()).strip(" :-־‑–")).strip(" :")
            name = label or "כרטיס"
            if prices:
                tiers += [TicketTier(name=name, price=p, is_free=p == 0) for p in prices if p is not None]
            elif s.free_word in part:
                tiers.append(TicketTier(name=part, price=0, is_free=True))
    return tiers


def _lineup_names(item: str, s: EfoKulamSettings) -> list[str]:
    """One chip -> names: "✓ לפני ואחרי: IMRI, NASGI • TOLED" -> IMRI, NASGI, TOLED (B2B is split by enrichment)."""
    text = " ".join(item.replace("✓", " ").split())
    text = text.rpartition(":")[2].strip()  # "אורחת מיוחדת: אן לואיז", "אפטר טראנס: ØFEK"
    if not re.search(r"[^\W\d_]", text) or text.casefold() in {w.casefold() for w in s.lineup_skip}:
        return []  # a time slot ("17:30-18:30") or a placeholder
    parts = [p.strip() for p in re.split(r"[,•]", text) if p.strip()]
    # "עידן אסולין, בגלמה / כינור": after a Hebrew name the comma starts a note, not a second name.
    return parts[:1] if re.search(r"[א-ת]", parts[0]) else parts


def parse_artists(content: str | None, s: EfoKulamSettings) -> list[str]:
    """Names of the article's lineup block: the chips / list after its "ליינאפ" label."""
    if not content:
        return []
    labels = {w.casefold() for w in s.lineup_labels}
    names: list[str] = []
    for label in LexborHTMLParser(content).css("div"):
        if label.text(strip=True).casefold() not in labels:
            continue
        node = label.next
        while node is not None:
            # Leaf chips only (a node's css() matches the node itself too): "17:30-18:30" + name inside one slot.
            items = [n for n in node.css("span, li") if len(n.css("span, li")) == 1] if node.tag != "-text" else []
            if items:  # the chip row / list: the lineup, and the block ends here
                names += [name for n in items for name in _lineup_names(n.text(strip=True), s)]
                break
            text = node.text(strip=True) if node.tag != "-text" else ""
            if node.tag == "div" and text:
                break  # the next label box ("הלוקיישן", "מתי"): no list in this block
            if node.tag == "h3" and text and not text.endswith("?"):
                names += _lineup_names(text, s)  # a name per heading, each followed by its bio
            node = node.next
        if names:
            break
    return list(dict.fromkeys(names))


def parse_event(row: dict[str, Any], types: dict[int, str], s: EfoKulamSettings, timezone: str) -> ScrapedEvent | None:
    """One REST row -> event; None without an id, title or date."""
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    title = _title(row, s)
    stamp = to_int(meta.get("event-date"))
    if not row.get("id") or not title or not stamp:
        return None
    venue, address, country = split_address(clean_str(meta.get("address")), s)
    tz_name = local_zone(country, timezone) if country else timezone
    tz = zone(tz_name)
    # The epoch is the local wall clock read as UTC: same digits, the venue's zone.
    start = datetime.fromtimestamp(stamp, UTC).replace(tzinfo=tz)
    clock = _CLOCK.match(clean_str(meta.get("event-gate_open")) or "")
    if clock:
        start = start.replace(hour=int(clock.group(1)) % 24, minute=int(clock.group(2)))
    genres = [types[t] for t in row.get("event-type") or [] if t in types] or [s.base_genre]
    link = clean_str(meta.get("ticket_link"))
    content = (row.get("content") or {}).get("rendered")
    return ScrapedEvent(
        source=EfoKulamScraper.name,
        source_event_id=str(row["id"]),
        url=str(row.get("link") or ""),
        title=title,
        description=html_to_text(meta.get("short_description")) or html_to_text(content),
        start_at=start,
        time_known=clock is not None,
        venue_name=venue,
        address=address,
        country=country,
        time_zone=tz_name if country else None,
        source_genres=list(dict.fromkeys(g.lower() for g in genres)),
        images=_image(row),
        tickets=parse_tickets(content, s),
        artists=parse_artists(content, s),
        extra={"purchase_url": link} if link and link.startswith("http") else {},
    )
