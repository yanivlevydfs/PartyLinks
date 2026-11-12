"""Application configuration.

Values come from a YAML file (``config/settings.yaml`` by default, or the path in
``PARTYLINKS_CONFIG``) and can be overridden by ``PARTYLINKS_*`` environment
variables using ``__`` as the nesting delimiter.
"""

from __future__ import annotations

import json
import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from partylinks.accounts.config import AccountsSettings
from partylinks.artist_filter.config import ArtistFilterSettings
from partylinks.artist_links.config import ArtistLinksSettings
from partylinks.bot.config import BotSettings
from partylinks.config_store.source import OverridesSource
from partylinks.evening.config import EveningSettings
from partylinks.event_text.config import EventTextSettings
from partylinks.holiday_pages.config import HolidayPagesSettings
from partylinks.home_rails.config import HomeRailsSettings
from partylinks.lines.config import LinesSettings
from partylinks.mail.config import MailSettings
from partylinks.pace import PaceSettings
from partylinks.placeinfo.config import PlaceInfoSettings
from partylinks.popularity.config import PopularitySettings
from partylinks.similar.config import SimilarSettings
from partylinks.smartrails.config import SmartRailsSettings
from partylinks.weekly_top.config import WeeklyTopSettings

CONFIG_ENV_VAR = "PARTYLINKS_CONFIG"
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
# Relative paths in the config (DB file, media, inboxes) resolve against the project
# root, so commands work from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def project_path(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else PROJECT_ROOT / path


class LoggingSettings(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Debug mode: DEBUG level, every HTTP request (not only failed ones) and the HTTP client's own
    # lines. Also switchable at runtime from /admin, until the next restart.
    debug: bool = False
    # text: for people; json: one object per line for log platforms (Railway, Datadog, ...).
    format: Literal["text", "json"] = "text"
    access_log: bool = True  # HTTP request lines: only server errors (5xx) unless debug is on
    # Optional rotating log file (relative to the project); unset on hosts that collect stdout.
    file: str | None = None
    file_max_mb: int = Field(10, ge=1)
    file_backups: int = Field(5, ge=0)
    # One status line (uptime, events, runs, scheduler, clicks) every N minutes; 0 = off.
    status_every_minutes: int = Field(30, ge=0)


class DatabaseSettings(BaseModel):
    # PostgreSQL URL (production: PARTYLINKS_DATABASE__URL). Empty = the local server (localpg.py): an
    # embedded PostgreSQL in local_dir, database local_database, for localhost and tests.
    url: str = ""
    local_dir: str = "data/pgdata"
    local_database: str = "partylinks"
    echo: bool = False
    # Connection pool. Source threads and page requests share it:
    # pool_size stays open, max_overflow more on bursts, pool_timeout seconds to wait for a free one.
    pool_size: int = Field(15, ge=1)
    max_overflow: int = Field(25, ge=0)
    pool_timeout: float = Field(30, gt=0)
    pool_recycle: int = Field(1800, ge=-1)  # seconds; reopen older connections (-1 = never)
    connect_timeout: int = Field(10, ge=1)  # seconds to open a connection
    # Server-side safety nets (milliseconds, 0 = off): a stuck statement or an abandoned transaction
    # is cancelled by PostgreSQL instead of holding row locks forever.
    statement_timeout_ms: int = Field(120_000, ge=0)
    idle_in_transaction_timeout_ms: int = Field(300_000, ge=0)
    # The regroup's whole-table reads (read-only: no row locks) compute in Python between statements; with
    # many sources running that took over 5 minutes and the timeout above killed every attempt (Nov 2026).
    regroup_idle_timeout_ms: int = Field(1_800_000, ge=0)
    lock_timeout_ms: int = Field(60_000, ge=0)
    # Parallel workers per query (PostgreSQL's max_parallel_workers_per_gather). 0: each query runs in one
    # process. Parallel plans share data through /dev/shm, which is small on Railway's Postgres; with
    # several sources saving at once it ran out ("could not resize shared memory segment ... No space
    # left on device") and the run failed. Serial, the whole-table artwork join takes 0.4 s, not 0.3 s.
    max_parallel_workers_per_gather: int = Field(0, ge=0)
    # TCP keepalives (seconds idle before the first probe, between probes, probes before giving up):
    # a connection whose peer vanished is dropped in about a minute instead of hanging.
    keepalive_idle: int = Field(30, ge=1)
    keepalive_interval: int = Field(10, ge=1)
    keepalive_count: int = Field(3, ge=1)
    application_name: str = "partylinks"  # shown in pg_stat_activity
    # Share of each events page filled on insert: the rest is room for in-place (HOT) updates.
    events_fillfactor: int = Field(85, ge=10, le=100)
    # Same for ticket tiers: their "remaining" counters change on every scan and are in no index (HOT).
    ticket_tiers_fillfactor: int = Field(85, ge=10, le=100)
    # Created at startup when missing; one that cannot be created is logged, never fatal.
    # pg_stat_statements: per-query timings (the library is preloaded on Railway's PostgreSQL).
    extensions: list[str] = ["pg_stat_statements"]
    # Writers run concurrently on row locks; true queues this process's writers one at a time.
    serialize_writes: bool = False
    # At startup, how long to wait for the database to answer (it may be restarting) before giving up;
    # keep it under the deploy's health-check timeout (railway.json: 120 s).
    startup_wait_s: float = Field(90, gt=0)
    # Retries of a write that hit a lock, deadlock, serialization failure or a dropped connection.
    retries: int = Field(4, ge=1)


class ApiSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    default_page_size: int = Field(50, ge=1)
    max_page_size: int = Field(200, ge=1)
    # Deepest page of a list: a person stops scrolling long before, a dump of every event needs all of them.
    max_page: int = Field(40, ge=1)
    # Paid placements (admin, placement results | both) matching a list's filters, shown first on its page 1.
    sponsored_results: int = Field(3, ge=0)
    # Cached pages and listings: rebuilt after cache_ttl_seconds, or after a write, but never younger than
    # cache_min_fresh_seconds. The worker commits every few seconds; without that floor every visit rebuilt its
    # page and scanned the events table (27k full scans in a few hours).
    cache_ttl_seconds: float = Field(60, gt=0)
    cache_min_fresh_seconds: float = Field(30, ge=0)
    # A page or listing that is not cached waits at most this long for a build slot, then a 503 (Retry-After):
    # crawlers opening cold pages must not hold every thread (the site went down twice on 2026-11-12). 0: no limit.
    cache_build_wait_seconds: float = Field(8, ge=0)
    # Rendered pages kept in memory (least recently used dropped first), and how long one is still served at
    # once while it is rebuilt in the background: a page built once never makes a visitor wait again that day.
    html_cache_mb: int = Field(128, ge=8)
    html_max_stale_seconds: float = Field(86400, ge=0)


class SchedulerSettings(BaseModel):
    enabled: bool = True
    run_on_startup: bool = False
    # A source whose last run a restart interrupted runs again at startup, unless this many in a row were.
    resume_max_in_a_row: int = Field(3, ge=1)
    misfire_grace_seconds: int = Field(600, ge=0)
    # /health flags an enabled source with no successful run for this long.
    stale_after_hours: float = Field(12, gt=0)
    # 0: no limit on a run's length (the default). A run is stopped only when it is stuck (the stall
    # watchdog below). Above 0: an optional emergency cap for every source, from the admin config tab.
    # The 3 h cap cut makore and shotgun mid-run while they still found events (2026-11-10).
    run_timeout_seconds: float = Field(0, ge=0)
    # The only stop for a live run: no progress for this long (no new event, no answer from the site,
    # no image stored, no post-processing step started) means stuck: stopped and recorded as failed.
    stall_timeout_seconds: float = Field(900, gt=0)
    # The scheduler and the sources in a worker process of their own (partylinks/worker), so scraping
    # never slows the site; false keeps them in the web process.
    separate_process: bool = True
    heartbeat_seconds: float = Field(5, gt=0)  # the worker publishes its state and reads commands this often
    worker_stale_seconds: float = Field(60, gt=0)  # no heartbeat for this long: /health says the worker is down
    command_timeout_seconds: float = Field(20, gt=0)  # how long the admin waits for the worker to answer
    restart_backoff_max_seconds: float = Field(60, gt=0)  # longest wait before restarting a crashing worker
    # A worker that ran this long before exiting restarts at once; one that dies sooner backs off (doubling).
    worker_stable_after_seconds: float = Field(600, gt=0)
    worker_stop_seconds: float = Field(30, gt=0)  # on shutdown, time for running sources to stop before a kill
    # A running worker whose heartbeat is this old is hung: stopped and restarted. Well above worker_stale_seconds,
    # so a busy worker is never killed; judged only after its first heartbeat.
    worker_hung_after_seconds: float = Field(300, gt=0)
    worker_commands_keep_hours: float = Field(24, gt=0)  # answered admin commands are deleted after this
    startup_stagger_seconds: float = Field(180, ge=0)  # run_on_startup: each source this long after the previous
    # Run manager (partylinks.runs): at most this many sources run at once, the rest wait in a queue.
    max_parallel_runs: int = Field(6, ge=1)
    # A bad run (failed, blocked, no events, or under low_count_ratio of the usual count) is run again
    # after each of these; still bad after the last, the source is paused (shown in the admin).
    retry_after_minutes: list[float] = Field(default_factory=lambda: [15, 60, 180])
    low_count_ratio: float = Field(0.3, ge=0, le=1)
    email_on_pause: bool = False  # also email the admins when a source is paused (the owner asked for none)
    low_count_min_usual: int = Field(10, ge=1)  # sources usually finding fewer are not judged by count
    # Schema drift: a run where more than this share of items failed (parse / validation / save / fetch,
    # the run's failures list) is bad too, once it has at least failure_min failures.
    max_failure_ratio: float = Field(0.15, ge=0, le=1)
    failure_min: int = Field(5, ge=1)


def validate_cron(value: str) -> str:
    """Reject a malformed crontab at config load instead of at scheduler start."""
    from apscheduler.triggers.cron import CronTrigger  # local: config stays import-light

    CronTrigger.from_crontab(value)
    return value


class HttpSettings(BaseModel):
    # A typo (timeout_secs) must fail loudly instead of silently using the default.
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = Field(30, gt=0)
    max_retries: int = Field(3, ge=0)
    backoff_seconds: float = Field(2, ge=0)
    request_delay_seconds: float = Field(0.5, ge=0)
    concurrency: int = Field(4, ge=1)
    # Slower when the site throttles (429 / 503), faster again when it doesn't (partylinks.pace).
    pace: PaceSettings = Field(default_factory=PaceSettings)
    user_agent: str = "Mozilla/5.0"
    accept_language: str = "he-IL,he;q=0.9,en;q=0.8"
    # Extra request headers, e.g. a partner access key agreed with a site owner.
    # Keep secrets in the environment: PARTYLINKS_SOURCES__<NAME>__HTTP__HEADERS='{"x-key": "..."}'
    headers: dict[str, str] = Field(default_factory=dict)
    # Outgoing proxy for scraping, e.g. "http://user:pass@host:port" or "socks5://host:1080"
    # (socks needs `pip install httpx[socks]`), so source sites never see the server's own IP.
    # Keep credentials in the environment: PARTYLINKS_HTTP__PROXY=...
    proxy: str | None = None
    # Proxy for every source (unless its block sets `proxied: false`), set only on Railway so the
    # sites never see the server's IP: PARTYLINKS_HTTP__SOURCE_PROXY=... A comma-separated
    # list is a failover pool: a source moves to the next URL only when the proxy itself fails.
    source_proxy: str | None = None
    # Israeli IPs for sources that refuse everything else (`israel_proxy: true` in their block):
    # PARTYLINKS_HTTP__ISRAEL_PROXY=http://ip:port,http://ip:port,... Each run starts at a random
    # one and rotates on failure; `source_proxy` follows as the last resort.
    israel_proxy: str | None = None
    # Live list of free Israeli proxies (geonode JSON), fetched at most every 30 minutes and put
    # before `israel_proxy`, so the pool follows the free proxies as they come and go.
    israel_proxy_feed: str | None = None
    # Our own Israeli exit (vpn/: CyberGhost on Railway's private network), used alone by sources with `vpn: true` (no free
    # proxies, no pool). Set on Railway production only: PARTYLINKS_HTTP__ISRAEL_VPN=http://vpn.railway.internal:8888
    israel_vpn: str | None = None
    # Speak as this browser at the TLS/HTTP2 level (curl_cffi, e.g. "chrome"), for sites whose bot
    # check refuses Python's handshake. The browser's own User-Agent replaces `user_agent`.
    impersonate: str | None = None

    @field_validator("headers", mode="before")
    @classmethod
    def _headers_from_json(cls, value: Any) -> Any:
        """Environment variables deliver the headers mapping as a JSON string."""
        return json.loads(value) if isinstance(value, str) else value


class TargetCitySettings(BaseModel):
    name: str = "Tel Aviv"
    aliases: list[str] = Field(default_factory=list)
    drop_other_cities: bool = False


class CitySettings(BaseModel):
    key: str  # URL slug: /city/<key>
    he: str  # canonical name stored on events
    en: str
    aliases: list[str] = Field(default_factory=list)
    # Events of this city whose coordinates lie farther than radius_m from center are mis-placed.
    center: tuple[float, float] | None = None
    radius_m: float = Field(10000, gt=0)


class LineupSettings(BaseModel):
    header_keywords: list[str] = Field(default_factory=list)
    max_names: int = Field(20, ge=1)
    max_name_length: int = Field(40, ge=1)
    max_name_words: int = Field(5, ge=1)
    # Hebrew-only lines longer than this are sentences, not names.
    max_hebrew_name_words: int = Field(3, ge=1)
    ignore: list[str] = Field(default_factory=list)
    # Words that may sit next to a header keyword in a real lineup header ("THE LINE UP", "הליינאפ שלנו").
    header_filler: list[str] = Field(default_factory=list)
    # A line with one of these words is a stage / area / date sub-header, never a name ("HIP HOP STAGE").
    not_name_words: list[str] = Field(default_factory=list)
    # A sentence with one of these speaks of past guests ("כבר ניגנו אצלם: Seth Troxler"): its known artists
    # are not spotted as this event's lineup.
    past_guest_words: list[str] = Field(default_factory=list)
    # "DJ <word>" in running text that is not a name ("DJ Mag", "DJ Night").
    inline_dj_stopwords: list[str] = Field(default_factory=list)
    # Set descriptions cut from a name: "<qualifier> <word>" ("AZVELOS - Birthday Set", "NARDO WICK — MAIN EVENT").
    set_words: list[str] = Field(default_factory=list)
    set_qualifiers: list[str] = Field(default_factory=list)
    min_known_length: int = Field(4, ge=2)  # shorter known names are never used as split points
    min_glued_length: int = Field(9, ge=4)  # shorter words are never split
    # A one-word curated name that is also a common English word ("Love", "Winter", "Blue", "Europe") is not
    # spotted in titles / text ("I LOVE COLLEGE PARTIES"), only taken from a source's own lineup. Its word
    # frequency (wordfreq's Zipf scale: "love" 5.8, "winter" 4.9, "Fisher" 4.0, "Solomun" 1.3); 0 = off.
    common_word_zipf: float = Field(4.5, ge=0)


class TimeSlot(BaseModel):
    name: str
    start_hour: int = Field(ge=0, le=23)
    # Exclusive; may be smaller than start_hour for slots that cross midnight.
    end_hour: int = Field(ge=0, le=24)

    def contains(self, hour: int) -> bool:
        if self.start_hour <= self.end_hour:
            return self.start_hour <= hour < self.end_hour
        return hour >= self.start_hour or hour < self.end_hour


class AreaSettings(BaseModel):
    """A part of the target city. Matched by keyword (street / neighbourhood names in the
    address or venue name) first, then by the nearest ``center`` within ``radius_m``."""

    key: str
    keywords: list[str] = Field(default_factory=list)
    center: tuple[float, float] | None = None
    radius_m: float = Field(1000, gt=0)


class RegionSettings(BaseModel):
    """A region of the country, for events outside the target city. Matched by a place name in
    the city / address / venue first, then by coordinates inside one of ``boxes``."""

    key: str
    places: list[str] = Field(default_factory=list)
    boxes: list[tuple[float, float, float, float]] = Field(default_factory=list)  # lat_min, lat_max, lng_min, lng_max


class EnrichmentSettings(BaseModel):
    # Hosts of profiles and posts, not sellers: an extra.purchase_url there is dropped, any source.
    social_hosts: list[str] = Field(default_factory=list)
    target_city: TargetCitySettings = Field(default_factory=TargetCitySettings)
    cities: list[CitySettings] = Field(default_factory=list)
    # Every other Israeli locality of at least this many people (GeoNames, places.py) is known too:
    # its spelling and its area on the map. Smaller ones are often plain words ("מגדל").
    places_min_population: int = Field(2000, ge=0)
    # A venue name whose events span more than this (degrees, ~100 m) is no one place: it ties no names together.
    venue_max_spread_degrees: float = 0.001
    # A venue name longer than this is a description a site put in the place field: no venue (and no geocoding).
    venue_name_max_chars: int = Field(130, ge=10)
    # Words a city field puts before the locality's name ("קיבוץ יגור", "מועצה אזורית גדרות"): dropped.
    city_prefixes: list[str] = Field(default_factory=list)
    # City fields that name no city ("ישראל"): no city.
    not_cities: list[str] = Field(default_factory=list)
    # Canonical city -> more spellings GeoNames lacks ("פרדס חנה", "oranit"), found like a town's names.
    city_aliases: dict[str, list[str]] = Field(default_factory=dict)
    genres: dict[str, list[str]] = Field(default_factory=dict)
    # More words per genre matched only against what the source declares (its categories), never the
    # title or description: too short or common to trust in free text ("AI", "ענן").
    declared_genres: dict[str, list[str]] = Field(default_factory=dict)
    # Umbrella genres: filtering or counting the parent ("electronic") includes every listed sub-genre.
    genre_families: dict[str, list[str]] = Field(default_factory=dict)
    # The genre filter's families ("g_parties"): each member, and a member family's sub-genres.
    genre_groups: dict[str, list[str]] = Field(default_factory=dict)
    detect_genres_from_text: bool = True
    # Sources whose own categories are precise: their text is not read for genres (a Funzing tour of
    # "the Greek market" is not Greek music).
    declared_genres_only: list[str] = Field(default_factory=list)
    # A source's genres when nothing else names one (a box office that sells only concerts).
    source_default_genres: dict[str, list[str]] = Field(default_factory=dict)
    # Words read in title + description only when nothing else gave the event a genre.
    fallback_genres: dict[str, list[str]] = Field(default_factory=dict)
    # Phrases removed from title + description before genres are read ("הכרטיס האלקטרוני").
    text_ignore: list[str] = Field(default_factory=list)
    # A source category that lumps kinds together ("מחול אמנות ומוזיקה (קלאסית, ג'ז)"): not read for genres,
    # its words would each add one. Any declared category starting with one of these is skipped.
    declared_ignore: list[str] = Field(default_factory=list)
    # Pages that are not an event, from any source ("MAKE SENSE | Ticket Management": a promoter's page of
    # passes to several parties, past ones included). Case-insensitive, anywhere in the title: dropped.
    skip_titles: list[str] = Field(default_factory=list)
    # A title with one of these is a talk, not a party: genres it describes ("the birth of house and
    # techno") are its subject, so only the genres its source declares are kept.
    talk_keywords: list[str] = Field(default_factory=list)
    # An event with one of these genres (or their sub-genres) is a talk too: its text gives no music genres.
    talk_genres: list[str] = Field(default_factory=list)
    # Genres (and their sub-genres) read from the text only when the source declares one of them.
    declared_text_genres: list[str] = Field(default_factory=list)
    # A source-only word (declared_genres) read from the text too, beside genres of "with" and none of "without".
    declared_genres_beside: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    # Genres read from the title only, never the description.
    title_genres: list[str] = Field(default_factory=list)
    lineup: LineupSettings = Field(default_factory=LineupSettings)
    time_slots: list[TimeSlot] = Field(default_factory=list)
    weekend_days: list[str] = Field(default_factory=list)
    # Areas of the target city, in priority order; events outside it get area "outside".
    areas: list[AreaSettings] = Field(default_factory=list)
    # Regions of the country, in priority order; an event outside the target city gets its region
    # key as area ("abroad" when its coordinates fall in no box, else "outside").
    regions: list[RegionSettings] = Field(default_factory=list)
    free_entry_keywords: list[str] = Field(default_factory=list)
    pay_at_door_keywords: list[str] = Field(default_factory=list)
    # A discount code is on offer ("קוד הנחה"); a 0 price tier that says so is a code redemption, not free entry.
    coupon_keywords: list[str] = Field(default_factory=list)

    def expand_genres(self, genres: list[str]) -> list[str]:
        """The genres plus every sub-genre of the umbrella ones and families among them, all the way down
        (dance -> latin_dance -> salsa), for filters."""
        out = list(dict.fromkeys(genres))
        for g in out:  # grows while walking: each sub-genre is expanded in turn
            out += [x for x in (*self.genre_groups.get(g, []), *self.genre_families.get(g, [])) if x not in out]
        return out

    def with_parents(self, genres: list[str]) -> list[str]:
        """An event's genres plus the umbrella genres and filter families they fall under (for counts)."""
        # All the way up: psytrance -> trance -> electronic.
        parents: list[str] = []
        while new := [
            p for p, subs in self.genre_families.items()
            if p not in genres and p not in parents and any(g in subs for g in (*genres, *parents))
        ]:  # fmt: skip
            parents += new
        known = {*genres, *parents}
        groups = [k for k, members in self.genre_groups.items() if k not in known and not known.isdisjoint(members)]
        return [*genres, *parents, *groups]


class DedupSettings(BaseModel):
    enabled: bool = True
    # difflib ratio between normalized titles (dates/emojis/punctuation/stopwords removed).
    title_similarity: float = Field(0.85, ge=0, le=1)
    max_start_gap_minutes: int = Field(60, ge=0)
    max_distance_meters: float = Field(300, ge=0)
    # Near-identical title at (almost) the same start time: allow a coarser location, e.g. an
    # aggregator that pins "Tel Aviv Port" instead of the club inside it.
    strong_title_similarity: float = Field(0.95, ge=0, le=1)
    strong_max_start_gap_minutes: int = Field(15, ge=0)
    # One date, hours at least this far apart and the same name: one site's "00:50" for the others' "23:50".
    night_typo_min_gap_hours: int = Field(18, ge=1)
    strong_max_distance_meters: float = Field(1500, ge=0)
    title_stopwords: list[str] = Field(default_factory=list)
    # Words box offices add to a show's name ("במופע", "סטנדאפ", "חנוכה"): titles equal without them are
    # identical. A title whose remaining words are all in the other's (one site adds "מחזמר הרפתקאות") is
    # one event only within subset_max_start_gap_minutes and max_distance_meters (or one venue), and only
    # when its words are distinctive: subset_min_words words, subset_min_chars letters ("techno" alone no).
    title_filler: list[str] = Field(default_factory=list)
    subset_min_words: int = Field(2, ge=1)
    subset_min_chars: int = Field(6, ge=1)
    subset_max_start_gap_minutes: int = Field(15, ge=0)
    # A price from a listing with no ticket tiers (a guide like Makore copying the box office) below this
    # share of the lowest tiered listing's price is a typo ("13" for "130"): not the merged event's price.
    secondhand_price_min_ratio: float = Field(0.3, ge=0, le=1)
    # Which listing represents a merged event (first = preferred). Unlisted sources rank last.
    source_priority: list[str] = Field(default_factory=list)
    # One site's short name and full name of one evening at one place this far apart are its doors and its
    # show, not two sessions (OGO: "מדע על הבר" 19:30, "מדע על הבר: התחדשות עירונית כהזדמנות" 20:00).
    doors_gap_minutes: int = Field(30, ge=0)
    # Sources made of many sites, one per tenant (the id's second part, "smarticket:lotus:1488"): two
    # tenants' pages are two box offices, not one site's sessions.
    tenant_sources: list[str] = Field(default_factory=lambda: ["smarticket", "smarticket_il", "munievents", "pres", "pres_il"])
    # Kinds one event carries on different sites: a kids' play is "kids" on one and "theatre" on another
    # (Haifa's "הענק וגנו", Nov 2026). Pairs listed here never part two listings.
    compatible_kinds: list[list[str]] = Field(
        default_factory=lambda: [["kids", "stage"], ["kids", "cinema"], ["kids", "music"], ["kids", "art"]]
    )
    # A city's own event pages: they say where the city's event really is, while its box office lists the
    # unit's default hall (Herzliya's Smarticket: "מרכז הקיימות" for a talk at Cafe Anat). Every listing of
    # an event with one of these takes its place (group_fill), and its listing joins the box office copy of
    # the same name, city and hour wherever the pins are (dedup._city_page). Empty = off.
    place_sources: list[str] = Field(
        default_factory=lambda: ["munievents", "haifamuni", "jerusalemmuni", "petahtikvamuni", "ramatganmuni", "sharepointmuni"]
    )
    # Sites with a page per promoter / entry time of one party (20:00 and 21:00 pages). Any other site's
    # pages at different hours are separate sessions (a workshop at 14:00 and 15:00) and never merge.
    promoter_page_sources: list[str] = Field(default_factory=list)
    # A party's listings may give entry times this far apart (20:00 doors on one site, 21:30 on another):
    # music only, and across sites only under a near-identical title (strong_title_similarity).
    party_max_start_gap_minutes: int = Field(90, ge=0)
    # Venues' own box offices: one session of a show there on a date sets the hour other sites' copies of
    # it (same venue, same name) group by - a guide's "14:00" for the venue's 20:30 is a typo, not a matinee.
    box_office_sources: list[str] = Field(default_factory=list)
    kinds: dict[str, list[str]] = Field(default_factory=dict)


class RetentionSettings(BaseModel):
    enabled: bool = True
    cron: str = "5 4 * * *"
    # 0 = only events from today (local) onwards; yesterday and earlier are over.
    keep_days_back: int = Field(0, ge=0)
    # A party that started last night and is still going stays until its end time
    # (only if it started within this many hours, so multi-week "season" listings don't).
    keep_in_progress: bool = True
    in_progress_max_hours: float = Field(16, gt=0)
    # An event with no end time is over this many hours after it starts (nobody arrives later). An all-day
    # listing (no hour: time_known false, or a 00:00 start) stays until the day ends instead.
    assumed_duration_hours: float = Field(3, gt=0)
    # A start further ahead than this is a source's broken date (2038-01-19 03:14:07 is the 32-bit clock's end,
    # "EXIT FESTIVAL 2025" sent as 2032): never imported, never shown, purged. Real tours sell ~1.5 years ahead.
    max_future_days: int = Field(730, ge=1)

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, value: str) -> str:
        return validate_cron(value)

    # Scrape run history older than this is deleted too.
    keep_runs_days: int = Field(30, ge=1)


class MediaSettings(BaseModel):
    download: bool = True
    # "database": image bytes in the event_images table; "filesystem": files under `directory`.
    storage: Literal["database", "filesystem"] = "database"
    directory: Path = Path("data/media")
    max_bytes: int = Field(15_000_000, ge=1)
    # A failed download (timeout, 503...) is retried on runs after this many hours.
    retry_failed_after_hours: float = Field(6, gt=0)
    # Resized WebP copies (640 / 1280 wide) stored beside each file: their encoder quality, and how
    # many threads make the copies files from before still lack (0 = none; startup, in the background).
    webp_quality: int = Field(78, ge=1, le=100)
    variant_threads: int = Field(2, ge=0)
    # Never SVG / HTML: they would run as pages on this site's origin.
    allowed_content_types: list[str] = Field(
        default_factory=lambda: ["image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"]
    )


class GeocodingSettings(BaseModel):
    """Coordinates for events whose source gives only an address (OpenStreetMap Nominatim)."""

    enabled: bool = True
    url: str = "https://nominatim.openstreetmap.org/search"
    # Nominatim's policy: identify the app, at most one request per second, cache results.
    user_agent: str = "SPOTX/0.1 (Israel event listings)"
    min_interval_seconds: float = Field(1.1, ge=1)
    max_lookups_per_run: int = Field(120, ge=0)
    country_codes: str = "il"
    # Prefer hits inside this box (lon1,lat1,lon2,lat2; Tel Aviv-Yafo) without excluding the rest.
    viewbox: str = "34.739,32.030,34.852,32.147"
    # Results requested per query; the first one that passes the checks is used.
    results_per_query: int = Field(5, ge=1, le=10)
    retry_failed_days: int = Field(14, ge=1)
    # One Nominatim request's limit; a failed request is not cached and ends the pass's lookups.
    timeout_seconds: float = Field(20, gt=0)
    # A hit further than this from the area its address keywords name is a same-name street elsewhere.
    max_area_distance_m: float = Field(2500, gt=0)
    # A source point farther than this from a small town (under places_min_population) named as the event's
    # city is wrong ("קיבוץ דן" on Tel Aviv's opera house); bigger towns use their own radius.
    small_town_radius_m: float = Field(3000, gt=0)
    # Manual pins, only for places OpenStreetMap doesn't know: venue-name / address keyword
    # (case-insensitive) -> [lat, lon]. Checked first.
    venues: dict[str, tuple[float, float]] = Field(default_factory=dict)


class AccessibilitySettings(BaseModel):
    """Accessibility statement (/accessibility). Israeli law (Equal Rights for Persons with
    Disabilities regulations, 2013, reg. 35) requires a reachable accessibility coordinator."""

    coordinator: str = ""
    email: str = ""
    phone: str = ""
    # Date the statement was last reviewed (shown on the page).
    updated: date | None = None

    @property
    def has_contact(self) -> bool:
        return bool(self.coordinator and (self.email or self.phone))


class PrivacySettings(BaseModel):
    """Privacy and cookie policy (/privacy). The Israeli Privacy Protection Law (Amendment 13) expects
    a way to reach the site about access, correction and deletion requests."""

    email: str = ""
    # Date the policy was last reviewed (shown on the page).
    updated: date | None = None


class IndexNowSettings(BaseModel):
    """partylinks.indexnow: new pages pushed to Bing / Yandex as they appear."""

    key: str = ""  # public; served at /<key>.txt. Empty: off
    cron: str = "40 * * * *"
    state_file: str = "data/indexnow-sent.txt"


class VerificationSettings(BaseModel):
    """Ownership tokens from the search consoles, rendered as <meta> tags in every page's head."""

    google: str = ""  # Search Console: HTML tag method, the content="..." value
    bing: str = ""  # Bing Webmaster Tools (msvalidate.01)
    yandex: str = ""  # Yandex Webmaster


class SiteSettings(BaseModel):
    # Public address of the website (no trailing slash): canonical links, sitemap, JSON-LD, share previews.
    base_url: str = "http://localhost:8000"
    # Ticket-site names on a card's picture before a "+N" chip (web/src/card_stores): 12 covered the picture.
    card_sources_shown: int = Field(2, ge=1, le=12)
    name: str = "SPOTX"
    # The artists index (/artists, the filter's artist list) and the sitemap's /artist/ pages list verified
    # artists only: names with a profile in the artist book (sellers' artist pages). Names read from free text
    # ("00h00 - 06h30", "16 Rue Saglio 67100") stay on their events but are not listed as artists.
    artists_verified_only: bool = True
    # Other upcoming events listed on an event's page (same venue, else same city): Google left thin event
    # pages "Crawled - currently not indexed"; these give each page context and links. 0: none.
    related_events: int = 8
    # Contact and partnerships address shown on /contact and /partners (falls back to privacy.email).
    email: str = ""
    # The site's own profiles elsewhere (its Telegram bot, social pages): Organization.sameAs in the home's JSON-LD.
    same_as: list[str] = Field(default_factory=list)
    accessibility: AccessibilitySettings = Field(default_factory=AccessibilitySettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    indexnow: IndexNowSettings = Field(default_factory=IndexNowSettings)
    verification: VerificationSettings = Field(default_factory=VerificationSettings)

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def contact_email(self) -> str:
        return self.email or self.privacy.email


class UtmSettings(BaseModel):
    """UTM tags added to ticket links in GET /go/... (see partylinks.utm)."""

    enabled: bool = True
    source: str = "spotx"
    medium: str = "affiliate"


class AdminSettings(BaseModel):
    # Bearer token for /admin and the mutating API routes. Keep it out of the YAML:
    # PARTYLINKS_ADMIN__TOKEN=... in the environment or in .env. Unset = localhost only.
    token: SecretStr | None = None


class SecuritySettings(BaseModel):
    """Request filtering in front of the site and API (see partylinks.security)."""

    enabled: bool = True
    # Reverse proxies (nginx, Caddy, a tunnel) whose X-Forwarded-For / -Proto are trusted for the
    # real client address. Anyone else sending those headers is ignored. Restart to apply.
    trusted_proxies: list[str] = Field(default_factory=lambda: ["127.0.0.1", "::1"])
    # Host names the site answers to (e.g. ["spotx.fun", "www.spotx.fun"]).
    # Empty = any host. localhost always works.
    allowed_hosts: list[str] = Field(default_factory=list)
    # Old addresses of the site: a request to one is sent (301) to site.base_url, keeping the path, so
    # links and search rankings move to the domain. Only when base_url is on another host, so while the
    # domain is not live (base_url still the Railway address) nothing moves. /health never redirects.
    redirect_hosts: list[str] = Field(default_factory=list)
    # Requests per client IP per minute; over it = 429. Admin API has its own, lower limit.
    rate_limit_per_minute: int = Field(300, ge=1)
    admin_rate_limit_per_minute: int = Field(30, ge=1)
    go_rate_limit_per_minute: int = Field(20, ge=1)
    # The listing data (/events, /categories...): a browsing person needs a few requests a minute,
    # a scraper thousands. Over it = 429, no strike (many phones share one carrier IP).
    data_rate_limit_per_minute: int = Field(60, ge=1)
    data_rate_limit_per_hour: int = Field(1200, ge=1)
    # /images/ requests that miss Cloudflare's cache, per IP: a person's cold pages need a few hundred,
    # copying every picture by id needs thousands.
    images_rate_limit_per_minute: int = Field(300, ge=1)
    images_rate_limit_per_hour: int = Field(3000, ge=1)
    # Vulnerability probes (/.env, /wp-login.php...), failed admin logins and rate-limit hits are
    # strikes; this many within ban_minutes bans the IP for ban_minutes.
    ban_after_strikes: int = Field(5, ge=1)
    ban_minutes: float = Field(60, gt=0)
    max_body_bytes: int = Field(1_000_000, ge=1)
    # Interactive API docs at /docs, /redoc and /openapi.json. Off = a smaller map for attackers.
    api_docs: bool = False
    # Crawlers that only harvest data (SEO tools, dataset scrapers): disallowed in robots.txt
    # and refused with 403 (matched case-insensitively in the User-Agent).
    blocked_bots: list[str] = Field(default_factory=lambda: [
        "AhrefsBot", "SemrushBot", "MJ12bot", "DotBot", "Bytespider", "PetalBot", "DataForSeoBot",
        "BLEXBot", "serpstatbot", "MegaIndex", "Barkrowler", "ZoominfoBot", "Scrapy",
    ])  # fmt: skip
    # Scripts and headless browsers: refused (403) on the JSON data API, /go/ and /images/ only, so AI assistants
    # browsing for a person can still read the pages and llms.txt.
    blocked_clients: list[str] = Field(default_factory=list)


class WeatherSettings(BaseModel):
    """Event forecasts (partylinks.weather): MET Norway's Locationforecast, cached per 0.1° cell."""

    url: str = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
    # One MET or Nominatim request's limit: a slow upstream never holds a visitor's request longer.
    timeout_seconds: float = Field(10, gt=0)
    min_ttl_seconds: float = Field(3600, gt=0)  # never refetch a place sooner, whatever MET's Expires says
    failure_ttl_seconds: float = Field(300, gt=0)  # a failed fetch / lookup is not retried for every card
    # Tries per MET fetch on a 5xx / network error / cut-off JSON (MET's edge does that in bursts).
    attempts: int = Field(3, ge=1, le=6)
    retry_seconds: float = Field(1, ge=0)
    # Whatever callers ask (the endpoint is public), at most this many upstream fetches per window.
    max_fetches: int = Field(120, ge=1)
    fetch_window_seconds: float = Field(600, gt=0)
    browser_cache_seconds: int = Field(900, ge=0)  # Cache-Control on a forecast
    unknown_place_cache_seconds: int = Field(86400, ge=0)  # ...and on "no such place" (looked up, not found)
    # Slots farther ahead are dropped: past about a week a forecast is a guess, so no weather beats a wrong one.
    max_days: float = Field(7, gt=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PARTYLINKS_",
        env_nested_delimiter="__",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    timezone: str = "Asia/Jerusalem"
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)
    enrichment: EnrichmentSettings = Field(default_factory=EnrichmentSettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)
    dedup: DedupSettings = Field(default_factory=DedupSettings)
    media: MediaSettings = Field(default_factory=MediaSettings)
    admin: AdminSettings = Field(default_factory=AdminSettings)
    site: SiteSettings = Field(default_factory=SiteSettings)
    geocoding: GeocodingSettings = Field(default_factory=GeocodingSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    utm: UtmSettings = Field(default_factory=UtmSettings)
    accounts: AccountsSettings = Field(default_factory=AccountsSettings)
    mail: MailSettings = Field(default_factory=MailSettings)
    weather: WeatherSettings = Field(default_factory=WeatherSettings)
    bot: BotSettings = Field(default_factory=BotSettings)  # the chat bot (partylinks.bot)
    popularity: PopularitySettings = Field(default_factory=PopularitySettings)  # likes / views / watching now
    home_rails: HomeRailsSettings = Field(default_factory=HomeRailsSettings)  # home rows (partylinks.home_rails)
    smart_rails: SmartRailsSettings = Field(default_factory=SmartRailsSettings)  # home rows (partylinks.smartrails)
    evening: EveningSettings = Field(default_factory=EveningSettings)  # plan the evening (partylinks.evening)
    event_text: EventTextSettings = Field(default_factory=EventTextSettings)  # how a description reads (partylinks.event_text)
    holiday_pages: HolidayPagesSettings = Field(default_factory=HolidayPagesSettings)  # partylinks.holiday_pages
    lines: LinesSettings = Field(default_factory=LinesSettings)  # weekly lines (partylinks.lines)
    artist_filter: ArtistFilterSettings = Field(default_factory=ArtistFilterSettings)  # the artists index's filter
    similar: SimilarSettings = Field(default_factory=SimilarSettings)  # artists like an artist (partylinks.similar)
    weekly_top: WeeklyTopSettings = Field(default_factory=WeeklyTopSettings)  # this week's top (partylinks.weekly_top)
    place_info: PlaceInfoSettings = Field(default_factory=PlaceInfoSettings)  # hours, booking of places (partylinks.placeinfo)
    artist_links: ArtistLinksSettings = Field(default_factory=ArtistLinksSettings)  # artists' Spotify / YouTube... (MusicBrainz)
    # Raw per-source blocks; each scraper validates its own block with its own model.
    # None = a block whose keys are all commented out (reported as invalid, not a crash).
    sources: dict[str, dict[str, Any] | None] = Field(default_factory=dict)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:  # fail at startup with a clear message, not on every request
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:  # ZoneInfoNotFoundError is a KeyError
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _home_rails_genres(self) -> Settings:
        if unknown := self.home_rails.unknown_genres(self.enrichment):
            raise ValueError(f"home_rails: unknown genres {', '.join(unknown)}")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_path = config_path()
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            OverridesSource(settings_cls),  # the admin's changes, from the database (partylinks.config_store)
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path, yaml_file_encoding="utf-8"),
        )


def config_path() -> Path:
    return project_path(os.environ.get(CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH))


@lru_cache
def get_settings() -> Settings:
    return Settings()
