"""Registry of source scrapers. Add a new source by writing its module and listing it here."""

from __future__ import annotations

from partylinks.config import Settings
from partylinks.scrapers.barby import BarbyScraper
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.chillz import ChillzScraper
from partylinks.scrapers.eventer import EventerScraper
from partylinks.scrapers.goout import GoOutScraper
from partylinks.scrapers.grayclub import GrayclubScraper
from partylinks.scrapers.selector import SelectorScraper
from partylinks.scrapers.tickchak import TickchakScraper
from partylinks.scrapers.zygo import ZygoScraper

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
    return SCRAPERS[name].settings_model.model_validate(settings.sources[name])


def configured_sources(settings: Settings) -> dict[str, SourceSettings]:
    return {name: source_settings(name, settings) for name in SCRAPERS if name in settings.sources}


def build_scraper(name: str, settings: Settings) -> BaseScraper:
    return SCRAPERS[name](source_settings(name, settings), settings.http, settings.timezone)
