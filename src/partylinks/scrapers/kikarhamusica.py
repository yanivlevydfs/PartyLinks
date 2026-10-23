"""Kikar HaMusica (kikar-hamusica.com/shows/he), Jerusalem's music square - daily live sets at its stage.

WordPress + The Events Calendar, so the generic Tribe source reads it: the JSON API under
``/shows/he/wp-json/tribe/events/v1/events`` lists every upcoming show (~90 over two months)
with local start/end, artwork and a music genre. No price, seller link or venue is filled in
(the shows are on no ticket site), so venue and address come from the config. robots.txt
allows everything.
"""

from __future__ import annotations

from partylinks.scrapers.tribe import TribeScraper


class KikarHamusicaScraper(TribeScraper):
    name = "kikarhamusica"
