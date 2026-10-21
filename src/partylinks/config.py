"""Application configuration.

Values come from a YAML file (``config/settings.yaml`` by default, or the path in
``PARTYLINKS_CONFIG``) and can be overridden by ``PARTYLINKS_*`` environment
variables using ``__`` as the nesting delimiter.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from datetime import date
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

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
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "WARNING"
    # Debug mode: DEBUG level, every HTTP request (not only failed ones) and the HTTP client's own
    # lines. Also switchable at runtime from /admin, until the next restart.
    debug: bool = False
    # text: for people; json: one object per line for log platforms (Railway, Datadog, ...).
    format: Literal["text", "json"] = "text"
    access_log: bool = True  # HTTP request lines: only failed ones (4xx/5xx) unless debug is on
    # Optional rotating log file (relative to the project); unset on hosts that collect stdout.
    file: str | None = None
    file_max_mb: int = Field(10, ge=1)
    file_backups: int = Field(5, ge=0)


class DatabaseSettings(BaseModel):
    url: str = "sqlite:///data/partylinks.db"
    echo: bool = False


class ApiSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    default_page_size: int = Field(50, ge=1)
    max_page_size: int = Field(200, ge=1)


class SchedulerSettings(BaseModel):
    enabled: bool = True
    run_on_startup: bool = False
    misfire_grace_seconds: int = Field(600, ge=0)
    # /health flags an enabled source with no successful run for this long.
    stale_after_hours: float = Field(12, gt=0)
    # Hard limit for one source run; a hung site is stopped and recorded as failed.
    run_timeout_seconds: float = Field(1800, gt=0)


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
    user_agent: str = "Mozilla/5.0"
    accept_language: str = "he-IL,he;q=0.9,en;q=0.8"
    # Extra request headers, e.g. a partner access key agreed with a site owner.
    # Keep secrets in the environment: PARTYLINKS_SOURCES__<NAME>__HTTP__HEADERS='{"x-key": "..."}'
    headers: dict[str, str] = Field(default_factory=dict)
    # Outgoing proxy for scraping, e.g. "http://user:pass@host:port" or "socks5://host:1080"
    # (socks needs `pip install httpx[socks]`), so source sites never see the server's own IP.
    # Keep credentials in the environment: PARTYLINKS_HTTP__PROXY=...
    proxy: str | None = None
    # Proxy for only the sources whose block sets `proxied: true` (sites that refuse the server's
    # IP), so one variable covers them all: PARTYLINKS_HTTP__SOURCE_PROXY=... A comma-separated
    # list is a failover pool: a source moves to the next URL only when the proxy itself fails.
    source_proxy: str | None = None

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
    # "DJ <word>" in running text that is not a name ("DJ Mag", "DJ Night").
    inline_dj_stopwords: list[str] = Field(default_factory=list)
    # Set descriptions cut from a name: "<qualifier> <word>" ("AZVELOS - Birthday Set", "NARDO WICK — MAIN EVENT").
    set_words: list[str] = Field(default_factory=list)
    set_qualifiers: list[str] = Field(default_factory=list)
    # Artist dictionary (its own SQLite file) used to split names a source glued together.
    artists_db: str = "data/artists.db"
    min_known_length: int = Field(4, ge=2)  # shorter known names are never used as split points
    min_glued_length: int = Field(9, ge=4)  # shorter words are never split


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
    target_city: TargetCitySettings = Field(default_factory=TargetCitySettings)
    cities: list[CitySettings] = Field(default_factory=list)
    genres: dict[str, list[str]] = Field(default_factory=dict)
    # Umbrella genres: filtering or counting the parent ("electronic") includes every listed sub-genre.
    genre_families: dict[str, list[str]] = Field(default_factory=dict)
    detect_genres_from_text: bool = True
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

    def expand_genres(self, genres: list[str]) -> list[str]:
        """The genres plus every sub-genre of the umbrella ones among them (for filters)."""
        return list(dict.fromkeys(x for g in genres for x in (g, *self.genre_families.get(g, []))))

    def with_parents(self, genres: list[str]) -> list[str]:
        """An event's genres plus the umbrella genres they fall under (for counts)."""
        parents = [p for p, subs in self.genre_families.items() if p not in genres and any(g in subs for g in genres)]
        return [*genres, *parents]


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
    strong_max_distance_meters: float = Field(1500, ge=0)
    title_stopwords: list[str] = Field(default_factory=list)
    # Which listing represents a merged event (first = preferred). Unlisted sources rank last.
    source_priority: list[str] = Field(default_factory=list)


class RetentionSettings(BaseModel):
    enabled: bool = True
    cron: str = "5 4 * * *"
    # 0 = only events from today (local) onwards; yesterday and earlier are over.
    keep_days_back: int = Field(0, ge=0)
    # A party that started last night and is still going stays until its end time
    # (only if it started within this many hours, so multi-week "season" listings don't).
    keep_in_progress: bool = True
    in_progress_max_hours: float = Field(16, gt=0)

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, value: str) -> str:
        return validate_cron(value)
    # Scrape run history older than this is deleted too.
    keep_runs_days: int = Field(30, ge=1)
    # SQLite only: give the space of deleted rows back to the OS after a purge.
    reclaim_space: bool = True


class MediaSettings(BaseModel):
    download: bool = True
    # "database": image bytes in the event_images table; "filesystem": files under `directory`.
    storage: Literal["database", "filesystem"] = "database"
    directory: Path = Path("data/media")
    max_bytes: int = Field(15_000_000, ge=1)
    # A failed download (timeout, 503...) is retried on runs after this many hours.
    retry_failed_after_hours: float = Field(6, gt=0)
    # Never SVG / HTML: they would run as pages on this site's origin.
    allowed_content_types: list[str] = Field(
        default_factory=lambda: ["image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"]
    )


class GeocodingSettings(BaseModel):
    """Coordinates for events whose source gives only an address (OpenStreetMap Nominatim)."""

    enabled: bool = True
    url: str = "https://nominatim.openstreetmap.org/search"
    # Nominatim's policy: identify the app, at most one request per second, cache results.
    user_agent: str = "Pulse/0.1 (Israel event listings)"
    min_interval_seconds: float = Field(1.1, ge=1)
    max_lookups_per_run: int = Field(120, ge=0)
    country_codes: str = "il"
    # Prefer hits inside this box (lon1,lat1,lon2,lat2; Tel Aviv-Yafo) without excluding the rest.
    viewbox: str = "34.739,32.030,34.852,32.147"
    # Results requested per query; the first one that passes the checks is used.
    results_per_query: int = Field(5, ge=1, le=10)
    retry_failed_days: int = Field(14, ge=1)
    # A hit further than this from the area its address keywords name is a same-name street elsewhere.
    max_area_distance_m: float = Field(2500, gt=0)
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


class SiteSettings(BaseModel):
    # Public address of the website (no trailing slash): canonical links, sitemap, JSON-LD, share previews.
    base_url: str = "http://localhost:8000"
    name: str = "Pulse"
    accessibility: AccessibilitySettings = Field(default_factory=AccessibilitySettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")


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
    # Host names the site answers to (e.g. ["partylinks.co.il", "www.partylinks.co.il"]).
    # Empty = any host. localhost always works.
    allowed_hosts: list[str] = Field(default_factory=list)
    # Requests per client IP per minute; over it = 429. Admin API has its own, lower limit.
    rate_limit_per_minute: int = Field(300, ge=1)
    admin_rate_limit_per_minute: int = Field(30, ge=1)
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
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path, yaml_file_encoding="utf-8"),
        )


def config_path() -> Path:
    return project_path(os.environ.get(CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH))


@lru_cache
def get_settings() -> Settings:
    return Settings()
