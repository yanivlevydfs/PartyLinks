"""Registry of source scrapers. Add a new source by writing its module and listing it here."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from partylinks.config import Settings
from partylinks.scrapers.agol import AgolScraper
from partylinks.scrapers.airdrop import AirdropScraper
from partylinks.scrapers.arthurmurray import ArthurMurrayScraper
from partylinks.scrapers.barak import BarakScraper
from partylinks.scrapers.barby import BarbyScraper
from partylinks.scrapers.bargiyora import BarGiyoraScraper
from partylinks.scrapers.basalon import BasalonScraper
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.beatradar import BeatRadarScraper
from partylinks.scrapers.beershevatheatre import BeerShevaTheatreScraper
from partylinks.scrapers.beithagefen import BeitHagefenScraper
from partylinks.scrapers.bimot import BimotScraper
from partylinks.scrapers.chillz import ChillzScraper
from partylinks.scrapers.cinemacity import CinemaCityScraper
from partylinks.scrapers.cinematheque_tlv import JerusalemCinemathequeScraper, TelAvivCinemathequeScraper
from partylinks.scrapers.comy import ComyScraper
from partylinks.scrapers.concertevents import ConcertEventsScraper
from partylinks.scrapers.dice import DiceScraper
from partylinks.scrapers.drop import DropScraper
from partylinks.scrapers.easytix import EasytixScraper
from partylinks.scrapers.efokulam import EfoKulamScraper
from partylinks.scrapers.epgb import EpgbScraper
from partylinks.scrapers.eventbrite import EventbriteScraper
from partylinks.scrapers.eventbuzz import EventbuzzScraper
from partylinks.scrapers.eventer import EventerScraper
from partylinks.scrapers.eventim import EventimScraper, ZappaScraper
from partylinks.scrapers.eventimlight import EventimLightScraper
from partylinks.scrapers.eventpro import EventProScraper
from partylinks.scrapers.feelbeit import FeelBeitScraper
from partylinks.scrapers.festivalim import FestivalimScraper
from partylinks.scrapers.fever import FeverScraper
from partylinks.scrapers.friendshist import FriendsHistScraper
from partylinks.scrapers.funzing import FunzingScraper
from partylinks.scrapers.ganeyyehoshua import GaneyYehoshuaScraper
from partylinks.scrapers.geektime import GeektimeScraper
from partylinks.scrapers.getin import GetinScraper
from partylinks.scrapers.glatticket import GlatticketScraper
from partylinks.scrapers.goout import GoOutScraper
from partylinks.scrapers.goshow import GoShowScraper
from partylinks.scrapers.gototlv import GotoTlvScraper
from partylinks.scrapers.grayclub import GrayclubScraper
from partylinks.scrapers.habama import HabamaScraper
from partylinks.scrapers.haderamuni import HaderaMuniScraper
from partylinks.scrapers.haezor import HaEzorScraper
from partylinks.scrapers.haifamuni import HaifaMuniScraper
from partylinks.scrapers.halutz import HalutzScraper
from partylinks.scrapers.hamenagen import HamenagenScraper
from partylinks.scrapers.hamesila import HaMesilaScraper
from partylinks.scrapers.hangar11 import Hangar11Scraper
from partylinks.scrapers.happytlv import HappyTlvScraper
from partylinks.scrapers.hatarbut import HatarbutScraper
from partylinks.scrapers.havanaclub import HavanaClubScraper
from partylinks.scrapers.holon import HolonScraper
from partylinks.scrapers.hotcinema import HotCinemaScraper
from partylinks.scrapers.hulyo import HulyoScraper
from partylinks.scrapers.hypd import HypdScraper
from partylinks.scrapers.inperson import InpersonScraper
from partylinks.scrapers.ironit import IronitScraper
from partylinks.scrapers.israelmusicfest import IsraelMusicFestScraper
from partylinks.scrapers.israelshow import IsraelShowScraper
from partylinks.scrapers.issta import IsstaScraper
from partylinks.scrapers.iwant import IwantScraper
from partylinks.scrapers.jaffatheatre import JaffaTheatreScraper
from partylinks.scrapers.jerusalemarts import JerusalemArtsScraper
from partylinks.scrapers.jerusalemmuni import JerusalemMuniScraper
from partylinks.scrapers.jerusalemtheatre import JerusalemTheatreScraper
from partylinks.scrapers.kikarhamusica import KikarHamusicaScraper
from partylinks.scrapers.kishreyteufa import KishreyTeufaScraper
from partylinks.scrapers.klika import KlikaScraper
from partylinks.scrapers.kolsadna import KolSadnaScraper
from partylinks.scrapers.kupatbravo import KupatbravoScraper
from partylinks.scrapers.kzattarbut import KzatTarbutScraper
from partylinks.scrapers.leaan import LeaanScraper
from partylinks.scrapers.lev import LevScraper
from partylinks.scrapers.levontin7 import Levontin7Scraper
from partylinks.scrapers.liveisrael import LiveIsraelScraper
from partylinks.scrapers.livetickets import LiveTicketsScraper
from partylinks.scrapers.livevents import LiveventsScraper
from partylinks.scrapers.luma import LumaScraper
from partylinks.scrapers.makore import MakoreScraper
from partylinks.scrapers.matnasnet import MatnasnetScraper
from partylinks.scrapers.meetup import MeetupScraper
from partylinks.scrapers.meety import MeetyScraper
from partylinks.scrapers.megaevents import MegaeventsScraper
from partylinks.scrapers.megatickets import MegaticketsScraper
from partylinks.scrapers.mevalim import MevalimScraper
from partylinks.scrapers.milgammuni import MilgamMuniScraper
from partylinks.scrapers.movieland import MovielandScraper
from partylinks.scrapers.mugics import MugicsScraper
from partylinks.scrapers.munievents import MuniEventsScraper
from partylinks.scrapers.music80s import Music80sScraper
from partylinks.scrapers.muzi import MuziScraper
from partylinks.scrapers.newacropolis import NewAcropolisScraper
from partylinks.scrapers.nikonitai import NikoNitaiScraper
from partylinks.scrapers.ogo import OgoScraper
from partylinks.scrapers.ogo_abroad import OgoDeScraper, OgoUsScraper
from partylinks.scrapers.onecity import OneCityScraper
from partylinks.scrapers.parks import ParksScraper
from partylinks.scrapers.parties247 import Parties247Scraper
from partylinks.scrapers.peoplecomputers import PeopleComputersScraper
from partylinks.scrapers.petahtikvamuni import PetahTikvaMuniScraper
from partylinks.scrapers.planet import PlanetScraper, RavHenScraper
from partylinks.scrapers.plusparty import PlusPartyScraper
from partylinks.scrapers.poalimatarim import PoalimAtarimScraper
from partylinks.scrapers.popit import PopitScraper
from partylinks.scrapers.posh import PoshScraper
from partylinks.scrapers.pres import PresIsraelScraper, PresScraper
from partylinks.scrapers.radical import RadicalScraper
from partylinks.scrapers.ramatganmuni import RamatGanMuniScraper
from partylinks.scrapers.reading3 import Reading3Scraper
from partylinks.scrapers.salsavida import SalsaVidaScraper
from partylinks.scrapers.secrettelaviv import SecretTelAvivScraper
from partylinks.scrapers.secrettelavivapp import SecretTelAvivAppScraper
from partylinks.scrapers.selector import SelectorScraper
from partylinks.scrapers.sharepointmuni import SharePointMuniScraper
from partylinks.scrapers.shotgun import ShotgunScraper
from partylinks.scrapers.showshappening import ShowsHappeningScraper
from partylinks.scrapers.skiddle import SkiddleScraper
from partylinks.scrapers.smarticket import SmarticketIsraelScraper, SmarticketScraper
from partylinks.scrapers.standupfactory import StandupFactoryScraper
from partylinks.scrapers.standupist import StandupistScraper
from partylinks.scrapers.swaptix import SwapTixScraper
from partylinks.scrapers.takeabreak import TakeABreakScraper
from partylinks.scrapers.tamuseum import TaMuseumScraper
from partylinks.scrapers.tarbut import TarbutScraper
from partylinks.scrapers.teder import TederScraper
from partylinks.scrapers.telaviv import TelAvivScraper
from partylinks.scrapers.tickchak import ArenasScraper, TickchakScraper
from partylinks.scrapers.ticket4u import Ticket4uScraper
from partylinks.scrapers.ticketeams import TicketeamsScraper
from partylinks.scrapers.tickethouse import TicketHouseScraper
from partylinks.scrapers.ticketingo import TicketingoScraper
from partylinks.scrapers.ticketmaster import TicketmasterScraper
from partylinks.scrapers.ticketplace import TicketPlaceScraper
from partylinks.scrapers.tiktik import TiktikScraper
from partylinks.scrapers.tiuli import TiuliScraper
from partylinks.scrapers.tlvjazzfest import TlvJazzFestScraper
from partylinks.scrapers.towerofdavid import TowerOfDavidScraper
from partylinks.scrapers.tribe import BabeBarScraper, GagarinScraper
from partylinks.scrapers.tzkrti import TzkrtiScraper
from partylinks.scrapers.viagogo import ViagogoScraper
from partylinks.scrapers.vibez import VibezScraper
from partylinks.scrapers.xceed import XceedScraper
from partylinks.scrapers.xplorer import XplorerScraper
from partylinks.scrapers.yadvashem import YadVashemScraper
from partylinks.scrapers.yellowsubmarine import YellowSubmarineScraper
from partylinks.scrapers.yotzim import YotzimScraper
from partylinks.scrapers.zoatlv import ZoaTlvScraper
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
        PoshScraper,
        EventbriteScraper,
        ShowsHappeningScraper,
        TicketingoScraper,
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
        PresIsraelScraper,
        SmarticketScraper,
        SmarticketIsraelScraper,
        SecretTelAvivScraper,
        TiktikScraper,
        GagarinScraper,
        TederScraper,
        MakoreScraper,
        IwantScraper,
        Music80sScraper,
        JerusalemTheatreScraper,
        DropScraper,
        AirdropScraper,
        HypdScraper,
        GetinScraper,
        TakeABreakScraper,
        DiceScraper,
        XceedScraper,
        SkiddleScraper,
        AgolScraper,
        YotzimScraper,
        LumaScraper,
        GeektimeScraper,
        PeopleComputersScraper,
        ParksScraper,
        MeetupScraper,
        ZappaScraper,
        BeerShevaTheatreScraper,
        NikoNitaiScraper,
        ArenasScraper,
        PlanetScraper,
        RavHenScraper,
        CinemaCityScraper,
        ConcertEventsScraper,
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
        OgoUsScraper,
        OgoDeScraper,
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
        PetahTikvaMuniScraper,
        HaderaMuniScraper,
        IronitScraper,
        JerusalemMuniScraper,
        StandupistScraper,
        SharePointMuniScraper,
        OgoScraper,
        MuziScraper,
        MatnasnetScraper,
        OneCityScraper,
        MilgamMuniScraper,
        HabamaScraper,
        FriendsHistScraper,
        PoalimAtarimScraper,
        TicketHouseScraper,
        BarakScraper,
        Ticket4uScraper,
        BarGiyoraScraper,
        HavanaClubScraper,
        Parties247Scraper,
        NewAcropolisScraper,
        BeitHagefenScraper,
        HaMesilaScraper,
        JerusalemArtsScraper,
        BimotScraper,
        IsraelShowScraper,
        KzatTarbutScraper,
        KolSadnaScraper,
        GlatticketScraper,
        SalsaVidaScraper,
        ArthurMurrayScraper,
        YadVashemScraper,
        BasalonScraper,
        RadicalScraper,
        HaEzorScraper,
        Levontin7Scraper,
        GoShowScraper,
        TzkrtiScraper,
        FeverScraper,
        TowerOfDavidScraper,
        TaMuseumScraper,
        SwapTixScraper,
        EventProScraper,
        JaffaTheatreScraper,
        Reading3Scraper,
        ZoaTlvScraper,
        YellowSubmarineScraper,
        HalutzScraper,
        Hangar11Scraper,
        MeetyScraper,
        SecretTelAvivAppScraper,
        KlikaScraper,
        GotoTlvScraper,
        InpersonScraper,
        IsraelMusicFestScraper,
        FeelBeitScraper,
        TlvJazzFestScraper,
        GaneyYehoshuaScraper,
        StandupFactoryScraper,
        TarbutScraper,
        FunzingScraper,
        EasytixScraper,
        TicketeamsScraper,
        BabeBarScraper,
        EventimLightScraper,
        MugicsScraper,
        PlusPartyScraper,
        EfoKulamScraper,
        LiveIsraelScraper,
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
