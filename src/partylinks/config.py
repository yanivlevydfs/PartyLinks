"""Application configuration.

Values come from a YAML file (``config/settings.yaml`` by default, or the path in
``PARTYLINKS_CONFIG``) and can be overridden by ``PARTYLINKS_*`` environment
variables using ``__`` as the nesting delimiter.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
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
# Relative paths in the config (DB file, media, inboxes) resolve against the project
# root, so commands work from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def project_path(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else PROJECT_ROOT / path


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

    @field_validator("headers", mode="before")
    @classmethod
    def _headers_from_json(cls, value: Any) -> Any:
        """Environment variables deliver the headers mapping as a JSON string."""
        return json.loads(value) if isinstance(value, str) else value


class TargetCitySettings(BaseModel):
    name: str = "Tel Aviv"
    aliases: list[str] = Field(default_factory=list)
    drop_other_cities: bool = False


class LineupSettings(BaseModel):
    header_keywords: list[str] = Field(default_factory=list)
    max_names: int = Field(20, ge=1)
    max_name_length: int = Field(40, ge=1)
    max_name_words: int = Field(5, ge=1)
    ignore: list[str] = Field(default_factory=list)


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


class EnrichmentSettings(BaseModel):
    target_city: TargetCitySettings = Field(default_factory=TargetCitySettings)
    genres: dict[str, list[str]] = Field(default_factory=dict)
    detect_genres_from_text: bool = True
    lineup: LineupSettings = Field(default_factory=LineupSettings)
    time_slots: list[TimeSlot] = Field(default_factory=list)
    weekend_days: list[str] = Field(default_factory=list)
    # Areas of the target city, in priority order; events outside it get area "outside".
    areas: list[AreaSettings] = Field(default_factory=list)
    free_entry_keywords: list[str] = Field(default_factory=list)
    pay_at_door_keywords: list[str] = Field(default_factory=list)


class DedupSettings(BaseModel):
    enabled: bool = True
    # difflib ratio between normalized titles (dates/emojis/punctuation/stopwords removed).
    title_similarity: float = Field(0.85, ge=0, le=1)
    max_start_gap_minutes: int = Field(60, ge=0)
    max_distance_meters: float = Field(300, ge=0)
    title_stopwords: list[str] = Field(default_factory=list)
    # Which listing represents a merged event (first = preferred). Unlisted sources rank last.
    source_priority: list[str] = Field(default_factory=list)


class RetentionSettings(BaseModel):
    enabled: bool = True
    cron: str = "5 4 * * *"
    keep_days_back: int = Field(1, ge=0)

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
    allowed_content_types: list[str] = Field(default_factory=list)


class SiteSettings(BaseModel):
    # Public address of the website (no trailing slash): canonical links, sitemap, JSON-LD, share previews.
    base_url: str = "http://localhost:8000"
    name: str = "PartyLinks"

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")


class AdminSettings(BaseModel):
    # Bearer token for /admin and the mutating API routes. Keep it out of the YAML:
    # PARTYLINKS_ADMIN__TOKEN=... in the environment or in .env. Unset = localhost only.
    token: SecretStr | None = None


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
    api: ApiSettings = Field(default_factory=ApiSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)
    enrichment: EnrichmentSettings = Field(default_factory=EnrichmentSettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)
    dedup: DedupSettings = Field(default_factory=DedupSettings)
    media: MediaSettings = Field(default_factory=MediaSettings)
    admin: AdminSettings = Field(default_factory=AdminSettings)
    site: SiteSettings = Field(default_factory=SiteSettings)
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
