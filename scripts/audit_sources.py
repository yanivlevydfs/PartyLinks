"""Audit every enabled source against the new-source checklist (docs/adding-a-source.md, section 9).

    python scripts/audit_sources.py                    # static checks, every enabled source
    python scripts/audit_sources.py tarbut cluberim    # only these sources
    python scripts/audit_sources.py --fails            # only the sources with a FAIL
    # production (read-only), inside the web container; the image has no scripts/, so stdin:
    railway ssh --service web -- "PYTHONIOENCODING=utf-8 python - --db" < scripts/audit_sources.py
    # the same run saved, then merged with the static checks of this checkout (and its genre keywords):
    railway ssh --service web -- "PYTHONIOENCODING=utf-8 python - --db --json" < scripts/audit_sources.py > prod.txt
    python scripts/audit_sources.py --prod prod.txt

One line per source, a PASS / WARN / FAIL (or "-": not checked) per check, then a line per finding.

Static checks (this checkout):
  reg    in the ``SCRAPERS`` registry
  name   a display name in ``web/src/sourceNames.json``
  store  an entry in ``STORES`` (``web/src/pages/Stores.tsx``)
  alias  a ``search.source_aliases`` entry
  docs   a ``docs/sources.md`` row (WARN: its times differ from the cron)
  cron   a cron, on a minute and hour no other enabled source uses (check_source.py's rule)
  test   ``tests/test_<name>.py`` or the test file of its module
  skip   ``skip_title_words`` in its block (WARN without; with --db, FAIL when titles hold class words)
  subs   tenants / hosts / sites with neither a city nor a venue (WARN; --db decides per tenant)
  keys   no third-party key or token in its saved pages (``tests/fixtures/<name>/``, its module's too): a
         Google ``AIza...`` key, a JWT, AWS / Stripe / GitHub / Slack tokens, a ``key=`` / ``token=`` value.
         Blank them before the commit (``key=REDACTED_THIRD_PARTY_KEY``): GitHub secret scanning flags
         them. Every other file under tests/fixtures is swept too (a FAIL line under the table).

Production checks (``--db``; read-only: one READ ONLY transaction of SELECTs, and file stats), over the
source's upcoming events (``db.upcoming_filter``):
  events  how many (FAIL at 0, unless ``may_be_empty``; "-" when production does not run the source yet)
  genre   no genre at all: WARN above 0%, FAIL above 5%
  city    no city: WARN below 100%, FAIL below 95% (docs: below 100% needs a fix or a stated reason)
  map     no coordinates: FAIL below 90% on the map (docs: below ~90% needs a fix or a stated reason)
  art     an artwork URL but no stored image: WARN above 2%, FAIL above 10%
  ctype   stored images without ``content_type``: FAIL at any
  dlerr   image rows whose download failed (``error`` set): WARN above 5%, FAIL above 20%
  webp    a sample of stored images: the 640 and 960 WebP copies on the volume (a 1280 copy not yet shrunk
          stands in for the 960 one, ``MediaStore.copy_path``): WARN at any missing, FAIL above 10%
  run     the last finished run: FAIL failed / blocked or older than 26 hours, WARN partial; paused = FAIL
  pins    an Israeli venue with 10+ upcoming events and none of them on the map: FAIL (pin it in
          ``geocoding.venues``)
  cats    a declared category (``source_genres``) that matches no genre keyword while its events have no
          genre: FAIL; unmapped values whose events got a genre from the text: WARN
  tenant  a tenant / host with 5+ events, over half without a city, no city in its config block, and its
          other events all in one city: FAIL (add that city); none in any city: FAIL; several: WARN

Stated reasons (``REASONS`` below) turn a low ``city`` / ``map`` into a PASS with the reason printed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_HERE = Path(__file__).resolve().parent.parent if not __file__.startswith("<") else Path.cwd()
ROOT = _HERE if (_HERE / "src" / "partylinks").is_dir() else Path.cwd()
sys.path.insert(0, str(ROOT / "src"))

from partylinks.config import get_settings
from partylinks.scrapers import SCRAPERS

GENRE_WARN, GENRE_FAIL = 0.0, 5.0  # % of upcoming events with no genre at all
CITY_WARN, CITY_FAIL = 100.0, 95.0  # % with a city
MAP_FAIL = 90.0  # % with coordinates
ART_WARN, ART_FAIL = 2.0, 10.0  # % with an artwork URL but no stored image
DLERR_WARN, DLERR_FAIL = 5.0, 20.0  # % of image rows whose download failed
WEBP_FAIL = 10.0  # % of the sample without both WebP copies
RUN_MAX_AGE = timedelta(hours=26)  # two runs a day: a day and a bit
PIN_MIN = 10  # upcoming events at one venue with no pin
TENANT_MIN = 5
# Title words of classes, courses and rentals (whole words; not English "course": a "four-course dinner"). With
# --db, a source without skip_title_words FAILs `skip` when CLASS_FAIL % of its titles hold them, else WARNs.
CLASS_WORDS = ["קורס", "קורסים", "חוג", "חוגי", "חוגים", "השכרת", "שיעור פרטי", "weekly class"]
CLASS_FAIL = 2.0
_LETTERS = "a-zA-Zא-ת"
STATIC = ["reg", "name", "store", "alias", "docs", "cron", "test", "skip", "subs", "keys"]
# Third-party keys and tokens a saved page may carry. A key=/token= value counts when it is 20+ characters
# with a digit and a letter, so a blanked "REDACTED_THIRD_PARTY_KEY" does not.
SECRET_PATTERNS = {
    "Google API key": r"AIza[0-9A-Za-z_-]{35}",
    "JWT": r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    "AWS key": r"AKIA[0-9A-Z]{16}",
    "Stripe key": r"(?:sk|rk)_live_[0-9A-Za-z]{16,}",
    "GitHub token": r"gh[pousr]_[0-9A-Za-z]{36}",
    "Slack token": r"xox[abprs]-[0-9A-Za-z-]{10,}",
    "key= / token= value": r"(?i)[?&;](?:api[_-]?key|apikey|key|access[_-]?token|token|secret|client_secret|sig|signature)"
    r"=(?=[0-9A-Za-z_-]*\d)(?=[0-9A-Za-z_-]*[A-Za-z])[0-9A-Za-z_-]{20,}",
}
PROD = ["events", "genre", "city", "map", "art", "ctype", "dlerr", "webp", "run", "pins", "cats", "tenant"]
SUB_ID = ("host", "url", "domain", "key")  # what names a tenant / host / site
SUB_PLACE = ("city", "venue_name", "venue", "address", "latitude")
# Stated reasons (docs: "city below 100% or map below ~90% needs a fix or a stated reason"): why a source's
# events stay without a city ("city") or off the map ("map"), and venues that get no pin ("no_pin": OSM does
# not know them, or the name is no place). Each one was checked against the events themselves (the audit of
# 2026-11-23); a gap our own pipeline leaves (a pinned venue without a city) is no reason.
_SECRET = "party lines at secret / nature spots: the site names a region or 'Israel' only"
REASONS: dict[str, dict[str, Any]] = {
    "selector": {"city": _SECRET, "map": _SECRET},
    "drop": {"city": _SECRET, "map": _SECRET},
    "vibez": {"city": _SECRET, "map": _SECRET + " ('Nature of Israel', 'To be announced')"},
    "fomo": {"city": _SECRET + " ('ישראל', 'צפון', 'בטבע היפה שלנו')", "map": _SECRET},
    "yotzim": {"city": _SECRET, "map": _SECRET + ", and festivals abroad named in Hebrew (Koh Phangan)"},
    "beatradar": {"city": _SECRET, "map": "festivals named by district only ('מחוז הצפון', 'North District')"},
    "zygo": {"city": _SECRET + " ('HaSharon, Israel', 'North District')"},
    "goout": {"city": _SECRET + " ('Hefer Valley Regional Council', 'Israel')"},
    "parties247": {"city": _SECRET},
    "efokulam": {"city": "one party at a club named without its town"},
    "liveisrael": {"city": _SECRET, "map": "docs/sources.md: secret spots keep only the tag city"},
    "airdrop": {"map": "festivals abroad named in Hebrew (Koh Phangan, Tulum, Brazil), and secret spots"},
    "ybz": {"map": "coach day tours that leave from a town ('יציאה מירושלים', 'כל הארץ'): no meeting place to pin; "
            "walking tours are geocoded from their meeting point"},
    "kolsadna": {"map": "workshops whose place is a town only ('טבריה', 'מודיעין') or told on sign-up ('כתובת מדוייקת "
                 "תינתן לנרשמים', 'בריכה מחוממת ליד בנימינה', 'תל אביב וכל הארץ'): a_location names no venue; "
                 "never a town centre as a pin (24.11.2026)"},
    "cluberim": {"map": "a festival named by its region only (B'KUSH, 'עמק חפר'): no venue on the page (24.11.2026)"},
    "buki": {"map": "night tours whose meeting point is told by phone on sign-up: buki.co.il gives no place, "
             "time or price (wp-json pages, 24.11.2026); the city only, never its centre as a pin"},
    "shimur": {"map": "heritage sites whose page has no map point (a redirect loop, no marker): geocoded by "
               "the site's name and town"},
    "posh": {"city": "secret locations ('revealed on the event date / after approval') and venues abroad named "
                     "by state or country only"},
    "megaevents": {"city": "packages abroad listed by country only (no venue, no town)"},
    "inperson": {"map": "docs/sources.md: dating tables hide the venue; the city is geocoded"},
    "luma": {"map": "Luma shows the address after registering only: many events give the city alone"},
    "geektime": {"city": "company offices named without a street or town", "map": "company offices named "
                 "without a street ('Microsoft Offices', 'Zenity Offices')"},
    "tiuli": {"city": "festivals and exhibitions named by region only", "map": "festivals and exhibitions named "
              "by town or region only"},
    "festivalim": {"map": "a festival is listed by its town only (it spans several venues)"},
    "munievents": {"map": "council listings that name the town only, trips 'outside the council', Zoom"},
    "ironit": {"map": "neighbourhood parks named by street and quarter, which OSM does not place"},
    "matnasnet": {"map": "community-centre listings with the town only, trips and outdoor activities"},
    "onecity": {"map": "municipal listings with the town only"},
    "eventpro": {"map": "community-centre listings with the town only (libraries, trips)"},
    "meety": {"city": "meetups whose meeting point changes", "map": "running / walking groups whose meeting "
              "point changes"},
    "smarticket": {
        "no_pin": {"סיורי מוזיאונים וגלריות": "tours: the meeting point changes", "טיולי חוץ": "trips: no venue"},
        # Tenants whose halls are in several villages or towns: a tenant city would mislabel the others. Their
        # halls name villages under places_min_population, which hall names never match (words like "יגור").
        "tenants": {host + ".smarticket.co.il": why for why, hosts in (
            ("a regional council's halls in its villages", ["brener", "eshkol", "eyz", "galil-elion", "gderot", "maianot",
                                                             "megido", "arava", "etzion", "hof-ashkelon", "misgav"]),
            ("a company playing in several towns", ["clipa", "ngvth"]),
        ) for host in hosts},
    },
    "makore": {"no_pin": {"בוסתן עברי": "OSM does not know it", "ניצחה הרוח": "OSM does not know it"}},
    "ogo": {"no_pin": {"גני תקווה": "a town named as the venue (never a town centre as a venue's pin)"}},
}  # fmt: skip


class Result:
    def __init__(self, name: str) -> None:
        self.name = name
        self.cells: dict[str, str] = {}
        self.notes: list[str] = []
        self.skipped: str | None = None

    def set(self, check: str, verdict: str, note: str | None = None) -> None:
        self.cells[check] = verdict
        if note:
            self.notes.append(f"{verdict} {check}: {note}")


def _pct(n: int, total: int) -> float:
    return 100.0 * n / total if total else 0.0


def cron_slots(cron: str) -> set[tuple[str, str]]:
    """(minute, hour) pairs; "*" or ranges count as clashing (check_source.py's rule)."""
    minute, hour = ([*cron.split(), "*", "*"])[:2]
    return {(m, h) for m in minute.split(",") for h in hour.split(",")}


def cron_clashes(name: str, crons: dict[str, str]) -> list[str]:
    mine = cron_slots(crons.get(name, ""))
    return [
        other
        for other, cron in crons.items()
        if other != name
        and any(m == m2 and (h == h2 or "*" in (h, h2)) for m, h in mine for m2, h2 in cron_slots(cron))
    ]


def cron_times(cron: str) -> str | None:
    """ "35 6,18 * * *" -> "06:35, 18:35" (the docs table's form); None for anything else."""
    parts = cron.split()
    if len(parts) < 2 or not parts[0].isdigit() or not all(h.isdigit() for h in parts[1].split(",")):
        return None
    return ", ".join(f"{int(h):02d}:{int(parts[0]):02d}" for h in parts[1].split(","))


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _tracked() -> set[str] | None:
    try:
        out = subprocess.run(["git", "ls-files", "src", "tests"], cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return set(out.stdout.split())


def _module_file(name: str) -> str | None:
    cls = SCRAPERS.get(name)
    if cls is None:
        return (
            f"src/partylinks/scrapers/{name}.py"
            if (ROOT / "src/partylinks/scrapers" / f"{name}.py").is_file()
            else None
        )
    return "src/" + cls.__module__.replace(".", "/") + ".py"


def sub_sources(block: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """A block's lists of tenants / hosts / sites: lists of mappings that each name a host, URL or key."""
    return {
        key: value
        for key, value in block.items()
        if isinstance(value, list) and value and all(isinstance(x, dict) and any(i in x for i in SUB_ID) for x in value)
    }


def sub_host(sub: dict[str, Any], block: dict[str, Any]) -> str | None:
    """The host a tenant's event URLs are on (Smarticket short names expand with its tenant_domain)."""
    for key in SUB_ID:
        value = sub.get(key)
        if not isinstance(value, str):
            continue
        if "://" in value:
            return urlsplit(value).netloc.lower().removeprefix("www.")
        if key == "host" and "." not in value and block.get("tenant_domain"):
            return f"{value}.{block['tenant_domain']}".lower()
        if "." in value:
            return value.lower().removeprefix("www.")
    return None


# ---------------------------------------------------------------------------------------- static
def static_checks(results: dict[str, Result], settings: Any, enabled: dict[str, dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    names_text = _read(ROOT / "web/src/sourceNames.json")
    names = json.loads(names_text) if names_text else None
    stores_text = _read(ROOT / "web/src/pages/Stores.tsx")
    stores = None
    if stores_text and (m := re.search(r"const STORES[^=]*=\s*\{(.*?)\n\};", stores_text, re.DOTALL)):
        stores = set(re.findall(r"(?<![\w\"'])([a-z0-9_]+):\s*\[", m.group(1)))
    docs = _read(ROOT / "docs/sources.md")
    tests = ROOT / "tests"
    test_texts = {p.name: p.read_text(encoding="utf-8") for p in tests.glob("test_*.py")} if tests.is_dir() else None
    aliases = settings.search.source_aliases
    crons = {n: str(b.get("cron") or "") for n, b in enabled.items()}
    tracked = _tracked()
    secrets = fixture_secrets(ROOT / "tests" / "fixtures")
    claimed: set[str] = set()

    if docs and (m := re.search(r"collects from (\d+) configured sources, (\d+) on", docs)):
        configured, on = len(settings.sources), len(enabled)
        if (int(m.group(1)), int(m.group(2))) != (configured, on):
            notes.append(
                f"WARN docs/sources.md header says {m.group(1)} configured, {m.group(2)} on; config has {configured}, {on}"
            )

    for name, block in enabled.items():
        r = results[name]
        module = _module_file(name)
        if tracked is not None and module and module not in tracked:
            r.skipped = f"its scraper {module} is not committed yet (another session's source)"
            continue
        r.set("reg", "PASS" if name in SCRAPERS else "FAIL", None if name in SCRAPERS else "not in SCRAPERS")
        if names is None:
            r.set("name", "-")
        else:
            r.set(
                "name", "PASS" if names.get(name) else "FAIL", None if names.get(name) else "no sourceNames.json entry"
            )
        if stores is None:
            r.set("store", "-")
        else:
            r.set("store", "PASS" if name in stores else "FAIL", None if name in stores else "no STORES entry")
        r.set(
            "alias",
            "PASS" if aliases.get(name) else "FAIL",
            None if aliases.get(name) else "no search.source_aliases entry",
        )
        if docs is None:
            r.set("docs", "-")
        elif row := re.search(rf"^\|\s*`{re.escape(name)}`\s*\|.*$", docs, re.MULTILINE):
            cells = [c.strip() for c in row.group(0).strip("|").split("|")]
            want = cron_times(crons[name])
            if want and len(cells) >= 3 and "UTC" not in cells[2] and cells[2] != want:
                r.set("docs", "WARN", f"the row says {cells[2]!r}, the cron runs at {want}")
            else:
                r.set("docs", "PASS")
        else:
            r.set("docs", "FAIL", "no docs/sources.md row")
        if not crons[name]:
            r.set("cron", "FAIL", "no cron")
        elif clash := cron_clashes(name, crons):
            r.set("cron", "FAIL", f"{crons[name]!r} shares a minute and hour with {clash}")
        else:
            r.set("cron", "PASS")
        if test_texts is None:
            r.set("test", "-")
        else:
            stem = Path(module).stem if module else name
            found = f"test_{name}.py" in test_texts or f"test_{stem}.py" in test_texts
            cls = SCRAPERS.get(name)
            # Tested in another module's file: its class or its module imported there.
            marks = [f'"{name}"', f"'{name}'", *([cls.__name__, f"{cls.__module__} import"] if cls else [])]
            found = found or any(m in t for t in test_texts.values() for m in marks)
            r.set("test", "PASS" if found else "FAIL", None if found else f"no tests/test_{name}.py")
        has_skip = "skip_title_words" in block or any(
            "skip_title_words" in s for v in sub_sources(block).values() for s in v
        )
        r.set("skip", "PASS" if has_skip else "WARN", None if has_skip else "no skip_title_words")
        bare = [
            f"{key}:{sub_host(s, block) or next((s[i] for i in SUB_ID if i in s), '?')}"
            for key, subs in sub_sources(block).items()
            for s in subs
            if not any(s.get(p) for p in SUB_PLACE)
        ]
        total = sum(len(v) for v in sub_sources(block).values())
        if not total:
            r.set("subs", "-")
        elif bare:
            r.set(
                "subs",
                "WARN",
                f"{len(bare)} of {total} with no city or venue: {', '.join(bare[:12])}{' ...' if len(bare) > 12 else ''}",
            )
        else:
            r.set("subs", "PASS")
        dirs = {name, Path(module).stem if module else name}
        found = {f: kinds for f, kinds in secrets.items() if f.split("/")[0] in dirs}
        claimed |= set(found)
        if not (ROOT / "tests" / "fixtures").is_dir():
            r.set("keys", "-")
        elif found:
            r.set(
                "keys",
                "FAIL",
                "blank before the commit: " + "; ".join(f"{f} ({', '.join(k)})" for f, k in found.items()),
            )
        else:
            r.set("keys", "PASS")
    for f, kinds in secrets.items():
        if f not in claimed:
            notes.append(f"FAIL keys: tests/fixtures/{f} holds {', '.join(kinds)}: blank it before the commit")
    return notes


def fixture_secrets(root: Path) -> dict[str, list[str]]:
    """Files under ``root`` (path relative to it) holding a third-party key or token, and which kinds."""
    found: dict[str, list[str]] = {}
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if kinds := [kind for kind, pattern in SECRET_PATTERNS.items() if re.search(pattern, body)]:
            found[path.relative_to(root).as_posix()] = kinds
    return found


# ---------------------------------------------------------------------------------------- production
def production(settings: Any, enabled: dict[str, dict[str, Any]], sample: int) -> dict[str, Any]:
    """Read-only numbers per source from the production database and the media volume."""
    from sqlalchemy import String, and_, case, exists, func, select, text

    from partylinks.db import Database, EventRow, FlagRow, ImageRow, ScrapeRunRow, upcoming_filter
    from partylinks.media import MediaStore

    db = Database(settings.database.url, options=settings.database)
    media = MediaStore(settings.media, db)
    up = upcoming_filter()
    no_genre = EventRow.genres.is_(None) | EventRow.genres.cast(String).in_(["[]", "null"])
    no_city = EventRow.city.is_(None) | (func.trim(EventRow.city) == "")
    no_coords = EventRow.latitude.is_(None) | EventRow.longitude.is_(None)
    israel = EventRow.country.is_(None) | (EventRow.country == "IL")
    stored_img = exists().where(ImageRow.event_id == EventRow.id, ImageRow.downloaded_at.is_not(None))
    any_img = exists().where(ImageRow.event_id == EventRow.id)
    has_art_url = EventRow.image_url.is_not(None) | any_img
    class_title = EventRow.title.op("~*")(f"(^|[^{_LETTERS}])({'|'.join(CLASS_WORDS)})($|[^{_LETTERS}])")
    out: dict[str, Any] = {"sources": {}, "deployed": sorted(SCRAPERS), "at": datetime.now(UTC).isoformat()}
    per: dict[str, dict[str, Any]] = defaultdict(dict)

    def count(cond: Any) -> Any:
        return func.count().filter(cond)

    with db.session(write=False) as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        rows = session.execute(
            select(
                EventRow.source,
                func.count(),
                count(no_genre),
                count(no_city),
                count(no_coords),
                count(~israel),
                count(has_art_url),
                count(has_art_url & ~stored_img),
                count(EventRow.image_url.is_(None) & ~any_img),
                count(class_title),
                count(no_city & ~no_coords),
            )
            .where(up)
            .group_by(EventRow.source)
        ).all()
        for source, total, ng, nc, nm, abroad, art, art_missing, no_art, classes, pinned_nc in rows:
            per[source].update(events=total, no_genre=ng, no_city=nc, no_coords=nm, abroad=abroad, art=art,
                               art_missing=art_missing, no_art=no_art, class_titles=classes, pinned_no_city=pinned_nc)  # fmt: skip
        rows = session.execute(
            select(
                EventRow.source,
                func.count(ImageRow.id),
                count(ImageRow.downloaded_at.is_not(None)),
                count(ImageRow.downloaded_at.is_not(None) & ImageRow.content_type.is_(None)),
                count(ImageRow.downloaded_at.is_(None) & ImageRow.error.is_not(None)),
            )
            .join(ImageRow, ImageRow.event_id == EventRow.id)
            .where(up)
            .group_by(EventRow.source)
        ).all()
        for source, rows_n, stored, no_ctype, errors in rows:
            per[source].update(image_rows=rows_n, images_stored=stored, no_ctype=no_ctype, dl_errors=errors)
        for source, err in session.execute(
            select(EventRow.source, func.min(ImageRow.error))
            .join(ImageRow, ImageRow.event_id == EventRow.id)
            .where(up, ImageRow.downloaded_at.is_(None), ImageRow.error.is_not(None))
            .group_by(EventRow.source)
        ).all():
            per[source]["dl_error_example"] = (err or "")[:120]
        # The newest stored images per source, for the WebP copies on the volume.
        ranked = (
            select(EventRow.source.label("source"), ImageRow.local_path.label("path"),
                   func.row_number().over(partition_by=EventRow.source, order_by=ImageRow.id.desc()).label("n"))
            .join(ImageRow, ImageRow.event_id == EventRow.id)
            .where(up, ImageRow.downloaded_at.is_not(None), ImageRow.local_path.is_not(None))
            .subquery()
        )  # fmt: skip
        paths: dict[str, list[str]] = defaultdict(list)
        for source, path in session.execute(select(ranked.c.source, ranked.c.path).where(ranked.c.n <= sample)).all():
            paths[source].append(path)

        # The last run, and the last finished one.
        def latest(*where: Any) -> Any:
            ranked = select(
                ScrapeRunRow.source, ScrapeRunRow.status, ScrapeRunRow.started_at, ScrapeRunRow.events_found,
                ScrapeRunRow.error,
                func.row_number().over(partition_by=ScrapeRunRow.source, order_by=ScrapeRunRow.started_at.desc()).label("n"),
            ).where(*where).subquery()  # fmt: skip
            return select(
                ranked.c.source, ranked.c.status, ranked.c.started_at, ranked.c.events_found, ranked.c.error
            ).where(ranked.c.n == 1)

        last_runs: Any = session.execute(latest()).all()  # rows of (source, status, started, found, error)
        for source, status, started, found, error in last_runs:
            per[source]["last_run"] = {"status": status, "started": started.isoformat(), "found": found}
        done_runs: Any = session.execute(latest(ScrapeRunRow.status != "running")).all()
        for source, status, started, found, error in done_runs:
            per[source]["last_done"] = {"status": status, "started": started.isoformat(), "found": found,
                                        "error": (error or "")[:160]}  # fmt: skip
        for (key,) in session.execute(select(FlagRow.key).where(FlagRow.key.like("paused:%"), FlagRow.on.is_(True))):
            per[key.removeprefix("paused:")]["paused"] = True
        # Venues with many upcoming events and no pin (Israel).
        venues = session.execute(
            select(EventRow.source, EventRow.venue_name, func.count(), count(~no_coords), func.min(EventRow.city))
            .where(up, israel, EventRow.venue_name.is_not(None))
            .group_by(EventRow.source, EventRow.venue_name)
            .having(and_(func.count() >= PIN_MIN, count(~no_coords) == 0))
        ).all()
        for source, venue, n, _pinned, city in venues:
            per[source].setdefault("unpinned", []).append([venue, n, city])
        # Declared categories: every value, its events and how many of them have no genre.
        value = func.jsonb_array_elements_text(EventRow.source_genres).table_valued("value").render_derived(name="sg")
        for source, val, n, ng in session.execute(
            select(EventRow.source, value.c.value, func.count(), count(no_genre))
            .select_from(EventRow)
            .join(value, text("true"))
            .where(up)
            .group_by(EventRow.source, value.c.value)
        ).all():
            per[source].setdefault("declared", []).append([val, n, ng])
        # Events per host, for the sources read through tenants / hosts / sites.
        multi = [n for n, b in enabled.items() if sub_sources(b)]
        host = func.lower(func.substring(EventRow.url, r"^https?://(?:www\.)?([^/:?#]+)"))
        if multi:
            for source, h, n, nc, cities in session.execute(
                select(EventRow.source, host, func.count(), count(no_city),
                       func.count(func.distinct(case((~no_city, EventRow.city)))))
                .where(up, EventRow.source.in_(multi))
                .group_by(EventRow.source, host)
            ).all():  # fmt: skip
                per[source].setdefault("hosts", []).append([h, n, nc, cities])
            for source, h, city in session.execute(
                select(EventRow.source, host, func.min(EventRow.city))
                .where(up, EventRow.source.in_(multi), ~no_city)
                .group_by(EventRow.source, host)
            ).all():
                per[source].setdefault("host_city", {})[h] = city
        distinct = (
            select(EventRow.source.label("source"), EventRow.title.label("title")).where(up, class_title).distinct()
        )
        distinct = distinct.subquery()
        titles = select(
            distinct.c.source, distinct.c.title,
            func.row_number().over(partition_by=distinct.c.source, order_by=distinct.c.title).label("n"),
        ).subquery()  # fmt: skip
        for source, title in session.execute(select(titles.c.source, titles.c.title).where(titles.c.n <= 6)).all():
            per[source].setdefault("class_examples", []).append(title)
    for source, found in paths.items():
        missing = orig_only = 0
        example = None
        for rel in found:
            small, big = media.copy_path(rel, 640), media.copy_path(rel, 960)
            if small.is_file() and big.is_file():
                continue
            if media.path_for(rel).is_file():
                orig_only += 1
            else:
                missing += 1
                example = example or rel
        per[source].update(webp_sample=len(found), webp_missing=missing, webp_original_only=orig_only,
                           webp_example=example)  # fmt: skip
    out["sources"] = per
    return out


def _declared_unmapped(settings: Any, declared: list[list[Any]]) -> list[list[Any]]:
    """The declared values no genre keyword matches (the Enricher's own patterns), most events first."""
    from partylinks.enrichment import _QUOTES, Enricher

    enricher = _enricher(settings, Enricher)
    ignore = tuple(p.lower() for p in settings.enrichment.declared_ignore)
    unmapped = []
    for val, n, ng in declared:
        low = str(val).lower().translate(_QUOTES)
        if low.startswith(ignore):
            continue
        if not any(p.search(low) for p in enricher._genres.values()) and not any(
            p.search(low) for p in enricher._declared.values()
        ):
            unmapped.append([val, n, ng])
    return sorted(unmapped, key=lambda x: (-x[2], -x[1]))


_ENRICHER: list[Any] = []


def _enricher(settings: Any, cls: Any) -> Any:
    if not _ENRICHER:
        _ENRICHER.append(cls(settings.enrichment, settings.timezone))
    return _ENRICHER[0]


def prod_checks(
    results: dict[str, Result], settings: Any, enabled: dict[str, dict[str, Any]], prod: dict[str, Any]
) -> None:
    per, deployed = prod["sources"], set(prod["deployed"])
    now = datetime.fromisoformat(prod["at"])
    for name, block in enabled.items():
        r = results[name]
        if r.skipped:
            continue
        if name not in deployed:
            for check in PROD:
                r.cells[check] = "-"
            r.notes.append("- production does not run it yet (not deployed)")
            continue
        p = per.get(name, {})
        total = p.get("events", 0)
        reasons = REASONS.get(name, {})
        if not total:
            verdict = "WARN" if block.get("may_be_empty") else "FAIL"
            r.set("events", verdict, "no upcoming events" + (" (may_be_empty)" if verdict == "WARN" else ""))
        else:
            r.set("events", "PASS")
        if total:
            ng = _pct(p.get("no_genre", 0), total)
            r.set("genre", "FAIL" if ng > GENRE_FAIL else "WARN" if ng > GENRE_WARN else "PASS",
                  f"{p['no_genre']}/{total} ({ng:.1f}%) without a genre" if ng > GENRE_WARN else None)  # fmt: skip
            city = 100 - _pct(p.get("no_city", 0), total)
            verdict = "FAIL" if city < CITY_FAIL else "WARN" if city < CITY_WARN else "PASS"
            if verdict != "PASS" and reasons.get("city"):
                r.set("city", "PASS", f"{city:.1f}% with a city: {reasons['city']}")
            else:
                r.set(
                    "city",
                    verdict,
                    f"{p['no_city']}/{total} without a city ({city:.1f}% with), {p.get('pinned_no_city', 0)} of them"
                    " pinned (their town is in the pin, not in the city field)"
                    if verdict != "PASS"
                    else None,
                )
            mapped = 100 - _pct(p.get("no_coords", 0), total)
            if mapped < MAP_FAIL and reasons.get("map"):
                r.set("map", "PASS", f"{mapped:.1f}% on the map: {reasons['map']}")
            else:
                r.set("map", "FAIL" if mapped < MAP_FAIL else "PASS",
                      f"{p['no_coords']}/{total} without coordinates ({mapped:.1f}% on the map)" if mapped < MAP_FAIL else None)  # fmt: skip
            art = _pct(p.get("art_missing", 0), p.get("art", 0))
            r.set("art", "FAIL" if art > ART_FAIL else "WARN" if art > ART_WARN else "PASS",
                  f"{p['art_missing']}/{p['art']} with artwork URLs have no stored image ({art:.1f}%)" if art > ART_WARN else None)  # fmt: skip
            if p.get("no_art") and p["no_art"] == total:
                r.notes.append("info art: no event has an artwork URL (the site has none, or the scraper passes none)")
        else:
            for check in ("genre", "city", "map", "art"):
                r.cells[check] = "-"
        if p.get("image_rows"):
            r.set("ctype", "FAIL" if p["no_ctype"] else "PASS",
                  f"{p['no_ctype']} stored images without content_type" if p["no_ctype"] else None)  # fmt: skip
            err = _pct(p["dl_errors"], p["image_rows"])
            r.set("dlerr", "FAIL" if err > DLERR_FAIL else "WARN" if err > DLERR_WARN else "PASS",
                  f"{p['dl_errors']}/{p['image_rows']} downloads failed ({err:.1f}%), e.g. {p.get('dl_error_example')!r}"
                  if err > DLERR_WARN else None)  # fmt: skip
        else:
            r.cells["ctype"] = r.cells["dlerr"] = "-"
        if p.get("webp_sample"):
            bad = p["webp_missing"] + p["webp_original_only"]
            share = _pct(bad, p["webp_sample"])
            note = (f"{p['webp_missing']} of {p['webp_sample']} sampled images lack a WebP copy and the file"
                    f" (e.g. {p['webp_example']}), {p['webp_original_only']} only the original") if bad else None  # fmt: skip
            r.set("webp", "FAIL" if share > WEBP_FAIL else "WARN" if bad else "PASS", note)
        else:
            r.cells["webp"] = "-"
        done = p.get("last_done")
        if p.get("paused"):
            r.set("run", "FAIL", "paused (runs kept failing)")
        elif not done and (p.get("last_run") or {}).get("status") == "running":
            r.set("run", "WARN", f"its first run is still going (since {p['last_run']['started'][:16]} UTC)")
        elif not done:
            r.set("run", "FAIL", "never ran")
        else:
            age = now - datetime.fromisoformat(done["started"])
            when = f"{done['status']} at {done['started'][:16]} UTC, {done['found']} found"
            if done["status"] in ("failed", "blocked"):
                r.set("run", "FAIL", f"{when}: {done['error']}")
            elif age > RUN_MAX_AGE and (p.get("last_run") or {}).get("status") != "running":
                r.set("run", "FAIL", f"last finished run {age.total_seconds() / 3600:.0f}h ago ({when})")
            elif done["status"] == "partial":
                r.set("run", "WARN", f"{when}: {done['error']}")
            else:
                r.set("run", "PASS")
            r.notes.append(f"info run: {when}")
        no_pin = reasons.get("no_pin", {})
        unpinned = sorted((v for v in p.get("unpinned", []) if v[0] not in no_pin), key=lambda v: -v[1])
        r.set("pins", "FAIL" if unpinned else "PASS" if total else "-",
              "no pin: " + "; ".join(f"{v} ({n}, {c or 'no city'})" for v, n, c in unpinned[:8]) if unpinned else None)  # fmt: skip
        for v, n, _ in p.get("unpinned", []):
            if v in no_pin:
                r.notes.append(f"info pins: {v} ({n}) has no pin: {no_pin[v]}")
        unmapped = _declared_unmapped(settings, p.get("declared", []))
        bad = [u for u in unmapped if u[2]]
        if bad:
            r.set(
                "cats",
                "FAIL",
                "maps to nothing, events without a genre: " + "; ".join(f"{v!r} {ng}/{n}" for v, n, ng in bad[:10]),
            )
        elif unmapped:
            r.set(
                "cats",
                "WARN",
                "maps to nothing (genres from the text): " + "; ".join(f"{v!r} {n}" for v, n, _ in unmapped[:8]),
            )
        else:
            r.set("cats", "PASS" if p.get("declared") else "-")
        if p.get("class_titles") and r.cells.get("skip") == "WARN":
            share = _pct(p["class_titles"], total)
            examples = "; ".join(repr(t) for t in p.get("class_examples", [])[:6])
            r.set("skip", "FAIL" if share >= CLASS_FAIL and p["class_titles"] >= 3 else "WARN",
                  f"no skip_title_words, {p['class_titles']} titles ({share:.1f}%) with class words: {examples}")  # fmt: skip
        tenant_checks(r, block, p)


def tenant_checks(r: Result, block: dict[str, Any], p: dict[str, Any]) -> None:
    subs = [s for v in sub_sources(block).values() for s in v]
    if not subs or not p.get("hosts"):
        r.cells["tenant"] = "-"
        return
    config = {sub_host(s, block): s for s in subs}
    fails, warns = [], []
    for host, n, nc, cities in p["hosts"]:
        sub = config.get(host)
        if sub is None or n < TENANT_MIN or nc * 2 <= n or sub.get("city"):
            continue
        if why := REASONS.get(r.name, {}).get("tenants", {}).get(host):
            r.notes.append(f"info tenant: {host}: {nc}/{n} without a city: {why}")
            continue
        if cities == 1:
            fails.append(f"{host}: {nc}/{n} without a city, the rest in {p.get('host_city', {}).get(host)!r}: add city")
        elif cities == 0:
            fails.append(f"{host}: {nc}/{n} without a city, none has one: add city or pin its halls")
        else:
            warns.append(f"{host}: {nc}/{n} without a city ({cities} cities: touring)")
    if fails:
        r.set("tenant", "FAIL", "; ".join(fails + warns))
    elif warns:
        r.set("tenant", "WARN", "; ".join(warns))
    else:
        r.set("tenant", "PASS")


# ---------------------------------------------------------------------------------------- report
def report(results: dict[str, Result], checks: list[str], fails_only: bool, global_notes: list[str]) -> int:
    width = max(len(n) for n in results) if results else 10
    print(f"{'source':<{width}}  " + " ".join(f"{c:<6}" for c in checks))
    totals: Counter[str] = Counter()
    failing = 0
    for name, r in results.items():
        if r.skipped:
            totals["skipped"] += 1
            if not fails_only:
                print(f"{name:<{width}}  skipped: {r.skipped}")
            continue
        verdicts = [r.cells.get(c, "-") for c in checks]
        has_fail = "FAIL" in verdicts
        failing += has_fail
        for c, v in zip(checks, verdicts, strict=True):
            totals[f"{c}:{v}"] += 1
        if fails_only and not has_fail:
            continue
        print(f"{name:<{width}}  " + " ".join(f"{v:<6}" for v in verdicts))
        for note in r.notes:
            if not fails_only or note.startswith("FAIL"):
                print(f"{'':<{width}}    {note}")
    print()
    for note in global_notes:
        print(note)
    audited = len(results) - totals["skipped"]
    print(f"{audited} sources audited, {failing} with a FAIL, {totals['skipped']} skipped")
    for c in checks:
        cells = {v: totals[f"{c}:{v}"] for v in ("PASS", "WARN", "FAIL", "-") if totals[f"{c}:{v}"]}
        print(f"  {c:<7} " + "  ".join(f"{v} {n}" for v, n in cells.items()))
    return 1 if failing else 0


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Hebrew venue names on Windows
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="sources to audit (default: every enabled one)")
    parser.add_argument("--db", action="store_true", help="production checks too (read-only; run inside the container)")
    parser.add_argument("--json", action="store_true", help="with --db: print the production numbers as one JSON line")
    parser.add_argument("--prod", type=Path, help="a saved --db --json output: merge its numbers with this checkout")
    parser.add_argument("--sample", type=int, default=20, help="stored images per source checked for WebP copies")
    parser.add_argument("--fails", action="store_true", help="only sources with a FAIL, and only their FAIL lines")
    args = parser.parse_args()
    settings = get_settings()
    enabled = {n: dict(b or {}) for n, b in settings.sources.items() if (b or {}).get("enabled", True)}
    if unknown := [n for n in args.names if n not in enabled]:
        parser.error(f"not an enabled source: {', '.join(unknown)}")
    if args.db and args.json:
        print("AUDIT-JSON " + json.dumps(production(settings, enabled, args.sample), ensure_ascii=False, default=str))
        return
    chosen = {n: enabled[n] for n in (args.names or enabled)}
    results = {n: Result(n) for n in chosen}
    notes = static_checks_for(results, settings, enabled, chosen)
    checks = STATIC
    prod = None
    if args.db:
        prod = production(settings, enabled, args.sample)
    elif args.prod:
        line = next(x for x in args.prod.read_text(encoding="utf-8").splitlines() if x.startswith("AUDIT-JSON "))
        prod = json.loads(line.removeprefix("AUDIT-JSON "))
    if prod is not None:
        prod_checks(results, settings, chosen, prod)
        checks = STATIC + PROD
        notes.append(f"production numbers from {prod['at'][:16]} UTC")
    sys.exit(report(results, checks, args.fails, notes))


def static_checks_for(
    results: dict[str, Result], settings: Any, enabled: dict[str, dict[str, Any]], chosen: dict[str, dict[str, Any]]
) -> list[str]:
    """Static checks of the chosen sources, with cron clashes against every enabled one."""
    everyone = {n: Result(n) for n in enabled}
    notes = static_checks(everyone, settings, enabled)
    for name in chosen:
        results[name] = everyone[name]
    return notes


if __name__ == "__main__":
    main()
