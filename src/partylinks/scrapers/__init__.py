"""Registry of source scrapers. Add a new source by writing its module and listing it here."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from partylinks.config import Settings
from partylinks.scrapers.barby import BarbyScraper
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.chillz import ChillzScraper
from partylinks.scrapers.epgb import EpgbScraper
from partylinks.scrapers.eventbuzz import EventbuzzScraper
from partylinks.scrapers.eventer import EventerScraper
from partylinks.scrapers.goout import GoOutScraper
from partylinks.scrapers.grayclub import GrayclubScraper
from partylinks.scrapers.hulyo import HulyoScraper
from partylinks.scrapers.leaan import LeaanScraper
from partylinks.scrapers.megatickets import MegaticketsScraper
from partylinks.scrapers.mevalim import MevalimScraper
from partylinks.scrapers.popit import PopitScraper
from partylinks.scrapers.pres import PresScraper
from partylinks.scrapers.selector import SelectorScraper
from partylinks.scrapers.smarticket import SmarticketScraper
from partylinks.scrapers.tickchak import TickchakScraper
from partylinks.scrapers.ticketmaster import TicketmasterScraper
from partylinks.scrapers.zygo import ZygoScraper

log = logging.getLogger(__name__)

SCRAPERS: dict[str, type[BaseScraper]] = {
    cls.name: cls
    for cls in (
        GoOutScraper,
        EventerScraper,
        ZygoScraper,
        TickchakScraper,
        SelectorScraper,
        BarbyScraper,
        GrayclubScraper,
        ChillzScraper,
        MevalimScraper,
        HulyoScraper,
        MegaticketsScraper,
        EventbuzzScraper,
        PopitScraper,
        EpgbScraper,
        LeaanScraper,
        TicketmasterScraper,
        PresScraper,
        SmarticketScraper,
    )
}


class UnknownSourceError(KeyError):
    pass


def source_settings(name: str, settings: Settings) -> SourceSettings:
    """Validate a source's config block with that source's own settings model."""
    if name not in SCRAPERS:
        raise UnknownSourceError(name)
    if name not in settings.sources:
        raise UnknownSourceError(f"{name} has no block under `sources` in the config")
    block = settings.sources[name]
    if block is None:
        raise ValueError(f"{name}: config block is empty")
    return SCRAPERS[name].settings_model.model_validate(block)


def configured_sources(settings: Settings) -> dict[str, SourceSettings]:
    """Valid source blocks. An invalid block disables only that source (see source_errors)."""
    valid: dict[str, SourceSettings] = {}
    for name in SCRAPERS:
        if name in settings.sources:
            try:
                valid[name] = source_settings(name, settings)
            except (ValidationError, ValueError, TypeError) as exc:
                log.error("source %s disabled: invalid config: %s", name, _short(exc))
    return valid


def source_errors(settings: Settings) -> dict[str, str]:
    """Config errors per source (and unknown source names) for /health and /sources."""
    errors: dict[str, str] = {name: "no scraper with this name" for name in settings.sources if name not in SCRAPERS}
    for name in SCRAPERS:
        if name in settings.sources:
            try:
                source_settings(name, settings)
            except (ValidationError, ValueError, TypeError) as exc:
                errors[name] = _short(exc)
    return errors


def _short(exc: Exception) -> str:
    return " ".join(str(exc).split())[:300]


def build_scraper(name: str, settings: Settings) -> BaseScraper:
    return SCRAPERS[name](source_settings(name, settings), settings.http, settings.timezone)
