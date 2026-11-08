"""Reading the three kinds of page, no network: HappyTLV's place page, Ontopo's venue page, a place's own site."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

from partylinks.parsing import clean_str, jsonld_nodes, schema_types, to_float

DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
HEBREW_DAYS = dict(zip(["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"], DAYS, strict=True))
_RATING = re.compile(r"(\d(?:\.\d)?)\s*(?:בגוגל\s*)?\(([\d,]+)\s*(?:ביקורות|reviews)")
_ONTOPO = re.compile(r"ontopo\.(?:com|co\.il)/(?:[a-z]{2}/[a-z]{2}/page/)?(\d{5,})")
_CID = re.compile(r"maps\.google\.com/\?cid=(\d+)")
_STATE = re.compile(r"window\.__INITIAL_STATE__=")
_CLOCK = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
_SITEMAP_PAGE = re.compile(r"<loc>\s*https://ontopo\.com/he/il/page/(\d+)\s*</loc>(?:\s*<lastmod>([^<]+)</lastmod>)?")


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if value else []


def phone_key(phone: Any) -> str | None:
    """"03-794-3529" and "+972 3 7943529" -> "037943529"."""
    digits = re.sub(r"\D", "", str(phone or ""))
    digits = "0" + digits[3:] if digits.startswith("972") else digits
    return digits if len(digits) >= 9 else None


def weekly(specs: Any) -> dict[str, list[str]] | None:
    """schema.org hours as {"Sunday": ["12:00-23:59"]}, each day in order; None when there are none."""
    days: dict[str, list[str]] = {}
    for spec in (s for s in _list(specs) if isinstance(s, dict)):
        opens, closes = str(spec.get("opens") or "")[:5], str(spec.get("closes") or "")[:5]
        if not (re.match(r"\d", opens) and re.match(r"\d", closes)):
            continue
        for day in _list(spec.get("dayOfWeek")):
            days.setdefault(str(day).rsplit("/", 1)[-1], []).append(f"{opens}-{closes}")
    return {d: sorted(days[d]) for d in DAYS if d in days} or None


# ----------------------------------------------------------------- HappyTLV
def happytlv_details(place: dict[str, Any], page_html: str) -> dict[str, Any]:
    """What a place's page says beyond name, address and pin, for the listing's ``extra``. The rating and
    hours are Google's as HappyTLV shows them ("לפי גוגל")."""
    tree = LexborHTMLParser(page_html)
    links = [a.attributes.get("href") or "" for a in tree.css("a[href]")]
    meta = tree.css_first('meta[name="description"]')
    head = tree.css_first("div.vsub")
    rating = _RATING.search((meta.attributes.get("content") or "") if meta else "") or (
        _RATING.search(head.text()) if head else None
    )
    ontopo = next((m[1] for link in links if (m := _ONTOPO.search(link))), None)
    tabit = next((link for link in links if "tabit" in urlsplit(link).netloc), None)
    cid = next((m[1] for link in links if (m := _CID.search(link))), None)
    offer = place.get("makesOffer") if isinstance(place.get("makesOffer"), dict) else {}
    service = offer.get("itemOffered") if isinstance(offer.get("itemOffered"), dict) else {}
    note = next(
        (
            clean_str((n.get("acceptedAnswer") or {}).get("text"))
            for n in jsonld_nodes(page_html)
            if "question" in schema_types(n) and str(n.get("name") or "").startswith("מה ההנחה")
        ),
        None,
    )
    area = head.css_first("span") if head else None
    found = {
        "hours": weekly(place.get("openingHoursSpecification")),
        "deal_hours": weekly(service.get("hoursAvailable")),
        "deal_note": note,
        "rating": to_float(rating[1]) if rating else None,
        "reviews": int(rating[2].replace(",", "")) if rating else None,
        "ontopo_slug": ontopo,
        "booking_url": tabit,
        "maps_url": f"https://maps.google.com/?cid={cid}" if cid else None,
        "amenities": [a["name"] for a in _list(place.get("amenityFeature")) if isinstance(a, dict) and a.get("value") and a.get("name")],
        "cuisine": [c for c in _list(place.get("servesCuisine")) if isinstance(c, str)],
        "menu_url": place.get("hasMenu") if isinstance(place.get("hasMenu"), str) else None,
        "links": [u for u in _list(place.get("sameAs")) if isinstance(u, str) and u.startswith("http")],
        "hood": clean_str(area.text()) if area else None,
    }
    return {k: v for k, v in found.items() if v}


def happytlv_cards(hub_html: str) -> dict[str, dict[str, Any]]:
    """The hub's card tags per bar page slug: {"yan": {"tags": ["דייט", ...]}}, one request for all of them."""
    return {
        slug: {"tags": tags.split("|")}
        for card in LexborHTMLParser(hub_html).css("article.vcard[data-slug]")
        if (slug := card.attributes.get("data-slug")) and (tags := card.attributes.get("data-tags"))
    }


# ----------------------------------------------------------------- Ontopo
def ontopo_sitemap(xml: str) -> dict[str, str | None]:
    """{slug: lastmod} of the Israeli pages."""
    return {slug: lastmod or None for slug, lastmod in _SITEMAP_PAGE.findall(xml)}


def _ontopo_hours(shifts: list[dict[str, Any]]) -> dict[str, list[str]] | None:
    """Ontopo cuts a day at each menu ("12:00 - 16:00", "16:00 - 17:00"): one range per unbroken opening."""
    days: dict[str, list[str]] = {}
    for shift in shifts:
        day = HEBREW_DAYS.get(str(shift.get("title") or "").removeprefix("יום ").strip())
        spans = [m.groups() for h in _list(shift.get("hours")) if not h.get("disabled") and (m := _CLOCK.search(str(h.get("hour"))))]
        merged: list[list[str]] = []
        for start, end in spans:
            if merged and merged[-1][1] == start:
                merged[-1][1] = end
            else:
                merged.append([start, end])
        if day and merged:
            days[day] = [f"{a}-{b}" for a, b in merged]
    return {d: days[d] for d in DAYS if d in days} or None


def ontopo_page(page_html: str) -> dict[str, Any] | None:
    """A venue page's fields, from the state the page itself renders from; {} when it has no venue, None
    when the page has no state at all (not Ontopo's page: a block page)."""
    start = _STATE.search(page_html)
    if not start:
        return None
    text = re.sub(r"\bundefined\b", "null", page_html[start.end() : page_html.find("</script>", start.end())])
    store = json.JSONDecoder().raw_decode(text)[0].get("websiteContentStore") or {}
    page = store.get("pageData") or {}
    if not page.get("title"):
        return {}
    items = ((store.get("pageContent") or {}).get("details") or {}).get("items") or []
    hours = next((i["openingHours"] for i in items if i.get("openingHours")), {})
    spot = (page.get("geolocation") or {}).get("coordinates") or [None, None]
    return {
        "title": clean_str(page["title"]),
        "latitude": spot[1],
        "longitude": spot[0],
        "phone": phone_key(page.get("phone")),
        "address": clean_str(page.get("address")),
        "website": clean_str(page.get("website")),
        "tags": [t for t in (clean_str(page.get("tag1")), clean_str(page.get("tag2"))) if t] or None,
        "hours": _ontopo_hours(hours.get("shifts") or []),
        "photos": [g["image"] for g in page.get("gallery") or [] if g.get("image")] or None,
    }


# ----------------------------------------------------------------- the place's own website
def site_details(page_html: str) -> dict[str, Any] | None:
    """Hours, phone and address from the first business node in the site's JSON-LD; None without one."""
    for node in jsonld_nodes(page_html):
        if schema_types(node) & {"website", "webpage", "breadcrumblist", "imageobject", "person", "postaladdress"}:
            continue
        hours = weekly(node.get("openingHoursSpecification"))
        phone = clean_str(node.get("telephone"))
        address = node.get("address") if isinstance(node.get("address"), dict) else {}
        street = clean_str(address.get("streetAddress"))
        if hours or phone or street:
            found = {"hours": hours, "phone": phone, "address": street}
            return {k: v for k, v in found.items() if v}
    return None
