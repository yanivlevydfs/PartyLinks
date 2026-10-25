"""Registry of source scrapers. Add a new source by writing its module and listing it here."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from partylinks.config import Settings
from partylinks.scrapers.airdrop import AirdropScraper
from partylinks.scrapers.barak import BarakScraper
from partylinks.scrapers.barby import BarbyScraper
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.beatradar import BeatRadarScraper
from partylinks.scrapers.beershevatheatre import BeerShevaTheatreScraper
from partylinks.scrapers.chillz import ChillzScraper
from partylinks.scrapers.cinemacity import CinemaCityScraper
from partylinks.scrapers.cinematheque_tlv import JerusalemCinemathequeScraper, TelAvivCinemathequeScraper
from partylinks.scrapers.comy import ComyScraper
from partylinks.scrapers.drop import DropScraper
from partylinks.scrapers.epgb import EpgbScraper
from partylinks.scrapers.eventbuzz import EventbuzzScraper
from partylinks.scrapers.eventer import EventerScraper
from partylinks.scrapers.eventim import EventimScraper, ZappaScraper
from partylinks.scrapers.festivalim import FestivalimScraper
from partylinks.scrapers.friendshist import FriendsHistScraper
from partylinks.scrapers.geektime import GeektimeScraper
from partylinks.scrapers.goout import GoOutScraper
from partylinks.scrapers.grayclub import GrayclubScraper
from partylinks.scrapers.habama import HabamaScraper
from partylinks.scrapers.haifamuni import HaifaMuniScraper
from partylinks.scrapers.hamenagen import HamenagenScraper
from partylinks.scrapers.happytlv import HappyTlvScraper
from partylinks.scrapers.hatarbut import HatarbutScraper
from partylinks.scrapers.holon import HolonScraper
from partylinks.scrapers.hotcinema import HotCinemaScraper
from partylinks.scrapers.hulyo import HulyoScraper
from partylinks.scrapers.issta import IsstaScraper
from partylinks.scrapers.jerusalemtheatre import JerusalemTheatreScraper
from partylinks.scrapers.kikarhamusica import KikarHamusicaScraper
from partylinks.scrapers.kishreyteufa import KishreyTeufaScraper
from partylinks.scrapers.kupatbravo import KupatbravoScraper
from partylinks.scrapers.leaan import LeaanScraper
from partylinks.scrapers.lev import LevScraper
from partylinks.scrapers.livetickets import LiveTicketsScraper
from partylinks.scrapers.livevents import LiveventsScraper
from partylinks.scrapers.luma import LumaScraper
from partylinks.scrapers.makore import MakoreScraper
from partylinks.scrapers.matnasnet import MatnasnetScraper
from partylinks.scrapers.meetup import MeetupScraper
from partylinks.scrapers.megaevents import MegaeventsScraper
from partylinks.scrapers.megatickets import MegaticketsScraper
from partylinks.scrapers.mevalim import MevalimScraper
from partylinks.scrapers.movieland import MovielandScraper
from partylinks.scrapers.munievents import MuniEventsScraper
from partylinks.scrapers.muzi import MuziScraper
from partylinks.scrapers.ogo import OgoScraper
from partylinks.scrapers.onecity import OneCityScraper
from partylinks.scrapers.peoplecomputers import PeopleComputersScraper
from partylinks.scrapers.planet import PlanetScraper, RavHenScraper
from partylinks.scrapers.poalimatarim import PoalimAtarimScraper
from partylinks.scrapers.popit import PopitScraper
from partylinks.scrapers.pres import PresScraper
from partylinks.scrapers.ramatganmuni import RamatGanMuniScraper
from partylinks.scrapers.secrettelaviv import SecretTelAvivScraper
from partylinks.scrapers.selector import SelectorScraper
from partylinks.scrapers.shotgun import ShotgunScraper
from partylinks.scrapers.smarticket import SmarticketScraper
from partylinks.scrapers.teder import TederScraper
from partylinks.scrapers.telaviv import TelAvivScraper
from partylinks.scrapers.tickchak import ArenasScraper, TickchakScraper
from partylinks.scrapers.tickethouse import TicketHouseScraper
from partylinks.scrapers.ticketmaster import TicketmasterScraper
from partylinks.scrapers.ticketplace import TicketPlaceScraper
from partylinks.scrapers.tiktik import TiktikScraper
from partylinks.scrapers.tiuli import TiuliScraper
from partylinks.scrapers.tribe import GagarinScraper
from partylinks.scrapers.viagogo import ViagogoScraper
from partylinks.scrapers.vibez import VibezScraper
from partylinks.scrapers.xplorer import XplorerScraper
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
        BeatRadarScraper,
        VibezScraper,
        ShotgunScraper,
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
        KupatbravoScraper,
        ComyScraper,
        EventimScraper,
        TicketmasterScraper,
        PresScraper,
        SmarticketScraper,
        SecretTelAvivScraper,
        TiktikScraper,
        GagarinScraper,
        TederScraper,
        MakoreScraper,
        JerusalemTheatreScraper,
        DropScraper,
        AirdropScraper,
        LumaScraper,
        GeektimeScraper,
        PeopleComputersScraper,
        MeetupScraper,
        ZappaScraper,
        BeerShevaTheatreScraper,
        ArenasScraper,
        PlanetScraper,
        RavHenScraper,
        CinemaCityScraper,
        HotCinemaScraper,
        LevScraper,
        TelAvivCinemathequeScraper,
        JerusalemCinemathequeScraper,
        MovielandScraper,
        KishreyTeufaScraper,
        TicketPlaceScraper,
        LiveventsScraper,
        IsstaScraper,
        HappyTlvScraper,
        MegaeventsScraper,
        ViagogoScraper,
        HamenagenScraper,
        HatarbutScraper,
        HolonScraper,
        KikarHamusicaScraper,
        XplorerScraper,
        LiveTicketsScraper,
        TiuliScraper,
        FestivalimScraper,
        TelAvivScraper,
        MuniEventsScraper,
        HaifaMuniScraper,
        RamatGanMuniScraper,
        OgoScraper,
        MuziScraper,
        MatnasnetScraper,
        OneCityScraper,
        HabamaScraper,
        FriendsHistScraper,
        PoalimAtarimScraper,
        TicketHouseScraper,
        BarakScraper,
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
